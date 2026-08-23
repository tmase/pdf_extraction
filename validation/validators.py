"""
Step 3 -- Data Validation and Reconciliation (no LLM).

Deterministic Python checks: type checks, then plausible static ranges,
then a *dynamic* statistical range built from the ground_truth table
(human-approved history plus any external reference data such as SEC Form
ADV filings) instead of hard-coded thresholds that need constant upkeep.
"""
import datetime as dt
import statistics
from dataclasses import dataclass

import config
from db.models import GroundTruth

# Baseline sanity bounds used only as a bootstrap before enough ground-truth
# history exists to compute a dynamic range (see dynamic_range_check).
STATIC_BOUNDS = {
    "monthly_net_return": (-100, 100),
    "ytd_net_return": (-100, 500),
    "long_exposure": (0, 400),
    "short_exposure": (-400, 0),
    "gross_exposure": (0, 800),
    "net_exposure": (-400, 400),
    "liquidity_30d": (0, 100),
}

MIN_GROUND_TRUTH_POINTS = 3
Z_SCORE_THRESHOLD = 3.0


@dataclass
class ValidationResult:
    passed: bool
    reason: str = ""
    classification: str | None = None  # missing/ambiguous/conflicting/incorrectly_scoped


def cast_value(field_name: str, raw_value):
    field_type = config.FIELD_SCHEMA.get(field_name, "string")
    if raw_value in (None, ""):
        return None
    text = str(raw_value).strip()
    if field_type in ("percent", "currency"):
        cleaned = (
            text.replace(",", "")
            .replace("%", "")
            .replace("$", "")
            .replace("MM", "")
            .replace("million", "")
            .replace("billion", "")
            .strip()
        )
        return float(cleaned)
    if field_type == "date":
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%m-%d-%Y"):
            try:
                return dt.datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                continue
        raise ValueError(f"unrecognized date format: {text!r}")
    return text


def type_check(field_name: str, raw_value) -> ValidationResult:
    if raw_value in (None, ""):
        return ValidationResult(False, "value is missing", "missing")
    try:
        cast_value(field_name, raw_value)
    except (ValueError, TypeError) as exc:
        return ValidationResult(
            False,
            f"could not parse {raw_value!r} as expected type "
            f"'{config.FIELD_SCHEMA.get(field_name)}': {exc}",
            "incorrectly_scoped",
        )
    return ValidationResult(True)


def static_range_check(field_name: str, value: float) -> ValidationResult:
    bounds = STATIC_BOUNDS.get(field_name)
    if bounds is None:
        return ValidationResult(True)
    lo, hi = bounds
    if not (lo <= value <= hi):
        return ValidationResult(
            False, f"{value} is outside the plausible range [{lo}, {hi}]", "incorrectly_scoped"
        )
    return ValidationResult(True)


def dynamic_range_check(session, field_name: str, value: float) -> ValidationResult:
    rows = [r.value for r in session.query(GroundTruth).filter(GroundTruth.field_name == field_name).all()]
    if len(rows) < MIN_GROUND_TRUTH_POINTS:
        return ValidationResult(True, "insufficient ground-truth history for a dynamic range")

    mean = statistics.fmean(rows)
    stdev = statistics.pstdev(rows) or 1e-6
    z = abs(value - mean) / stdev
    if z > Z_SCORE_THRESHOLD:
        return ValidationResult(
            False,
            f"{value} is {z:.1f} standard deviations from the historical mean "
            f"({mean:.2f} +/- {stdev:.2f}) for {field_name}",
            "conflicting",
        )
    return ValidationResult(True)


def validate_field(session, field_name: str, raw_value) -> ValidationResult:
    """Full Step 3 chain: type check -> static range -> dynamic (ground-truth) range."""
    result = type_check(field_name, raw_value)
    if not result.passed:
        return result

    field_type = config.FIELD_SCHEMA.get(field_name)
    if field_type not in ("percent", "currency"):
        return ValidationResult(True)

    value = cast_value(field_name, raw_value)

    result = static_range_check(field_name, value)
    if not result.passed:
        return result

    return dynamic_range_check(session, field_name, value)
