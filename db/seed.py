"""Default prompts and bootstrap ground-truth values, loaded on first init_db()."""
import config
from db.models import GroundTruth, PromptTemplate

DEFAULT_SYSTEM_PROMPT = """\
You are the Fund Analyst Agent for UVIMCO's risk report extraction pipeline.

You will be given the raw text (and any extracted tables) of a single fund
or portfolio risk report. Your job is to extract exactly these fields:

- fund_name: the fund or portfolio name
- reporting_date: the "as of" / reporting date (YYYY-MM-DD if possible)
- capital_base: fund-level capital base / NAV, as a number (no currency symbol)
- monthly_net_return: monthly net return, as a percent number (e.g. 1.8 for 1.8%)
- ytd_net_return: year-to-date net return, as a percent number
- long_exposure: long exposure, as a percent of NAV
- short_exposure: short exposure, as a percent of NAV
- gross_exposure: gross exposure, as a percent of NAV
- net_exposure: net exposure, as a percent of NAV
- top_sector: the single largest sector by net exposure
- top_region: the single largest region by net exposure
- liquidity_30d: percent of the portfolio liquidable within 30 days

For each field, return:
- "value": your best answer, or null if it truly cannot be found
- "source_context": the exact sentence or table header/row you took it from
- "confidence": your calibrated confidence from 0.0 to 1.0 that the value is
  correct and unambiguous. Use a LOW confidence (below 0.6) whenever the
  value is missing, inferred indirectly, stated more than once with
  conflicting numbers, or ambiguous about scope (e.g. share class vs. whole
  fund, gross vs. net).

Respond with ONLY a JSON object of the form:
{"fields": {"<field_name>": {"value": ..., "source_context": "...", "confidence": 0.0}, ...}}
"""

# Known-good historical values used to seed dynamic range checks (Step 3).
# In production this table is populated by prior human-approved extractions
# (see review.notifier / pipeline.orchestrator learning loop) plus external
# sources such as SEC Form ADV.
GROUND_TRUTH_SEED = [
    # field_name, value, source
    ("monthly_net_return", -1.2, "human_verified"),
    ("monthly_net_return", 0.4, "human_verified"),
    ("monthly_net_return", 1.1, "human_verified"),
    ("monthly_net_return", 2.3, "human_verified"),
    ("monthly_net_return", -0.6, "human_verified"),
    ("ytd_net_return", 3.5, "human_verified"),
    ("ytd_net_return", 7.8, "human_verified"),
    ("ytd_net_return", -2.1, "human_verified"),
    ("ytd_net_return", 5.0, "human_verified"),
    ("gross_exposure", 140.0, "human_verified"),
    ("gross_exposure", 165.0, "human_verified"),
    ("gross_exposure", 155.0, "human_verified"),
    ("gross_exposure", 172.0, "human_verified"),
    ("net_exposure", 35.0, "human_verified"),
    ("net_exposure", 42.0, "human_verified"),
    ("net_exposure", 38.5, "human_verified"),
    ("net_exposure", 45.0, "human_verified"),
    ("long_exposure", 90.0, "human_verified"),
    ("long_exposure", 100.0, "human_verified"),
    ("long_exposure", 105.0, "human_verified"),
    ("short_exposure", -55.0, "human_verified"),
    ("short_exposure", -60.0, "human_verified"),
    ("short_exposure", -65.0, "human_verified"),
    ("liquidity_30d", 70.0, "human_verified"),
    ("liquidity_30d", 82.0, "human_verified"),
    ("liquidity_30d", 91.0, "human_verified"),
    ("liquidity_30d", 76.0, "human_verified"),
]


def seed_prompts(session):
    existing = session.query(PromptTemplate).filter_by(name="fund_analyst_system_prompt").first()
    if not existing:
        session.add(
            PromptTemplate(
                name="fund_analyst_system_prompt",
                content=DEFAULT_SYSTEM_PROMPT,
                version=1,
                updated_by="system",
            )
        )


def seed_ground_truth(session):
    if session.query(GroundTruth).count() > 0:
        return
    for field_name, value, source in GROUND_TRUTH_SEED:
        session.add(GroundTruth(fund_name=None, field_name=field_name, value=value, source=source))
