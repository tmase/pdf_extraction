"""
Generates a handful of synthetic fund risk report PDFs so the pipeline can
be demoed end to end before real reports are dropped in. Not meant to be
realistic financial content -- just structured enough to exercise text
extraction, table extraction, the extractor/agent, and validation
(including deliberately-planted issues that should land in the review
queue).
"""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet

OUTPUT_DIR = Path(__file__).resolve().parent / "pdfs"


def _build_report(filepath: Path, data: dict):
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(filepath), pagesize=letter)
    story = []

    story.append(Paragraph(f"Fund Name: {data['fund_name']}", styles["Title"]))
    story.append(Spacer(1, 0.15 * inch))
    story.append(Paragraph(f"Reporting Date: {data['reporting_date']}", styles["Normal"]))
    story.append(Paragraph(f"Capital Base: ${data['capital_base']:,.0f}", styles["Normal"]))
    story.append(Spacer(1, 0.25 * inch))

    story.append(Paragraph("Performance Summary", styles["Heading2"]))
    story.append(Paragraph(f"Monthly Net Return: {data['monthly_net_return']}%", styles["Normal"]))
    story.append(Paragraph(f"YTD Net Return: {data['ytd_net_return']}%", styles["Normal"]))
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Exposure Summary", styles["Heading2"]))
    table_data = [
        ["Metric", "Value (% of NAV)"],
        ["Long Exposure", f"{data['long_exposure']}%"],
        ["Short Exposure", f"{data['short_exposure']}%"],
        ["Gross Exposure", f"{data['gross_exposure']}%"],
        ["Net Exposure", f"{data['net_exposure']}%"],
    ]
    table = Table(table_data, colWidths=[2.5 * inch, 2.5 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f4b7c")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
    ]))
    story.append(table)
    story.append(Spacer(1, 0.25 * inch))

    story.append(Paragraph("Concentration & Liquidity", styles["Heading2"]))
    story.append(Paragraph(f"Top Sector: {data['top_sector']}", styles["Normal"]))
    story.append(Paragraph(f"Top Region: {data['top_region']}", styles["Normal"]))
    story.append(Paragraph(f"Liquidity within 30 Days: {data['liquidity_30d']}%", styles["Normal"]))

    doc.build(story)


SAMPLES = [
    # A "clean" report -- every field should be found with high confidence
    # and pass validation (auto-approved end to end).
    dict(
        fund_name="Blue Ridge Long/Short Equity Fund",
        reporting_date="July 31, 2026",
        capital_base=482_000_000,
        monthly_net_return=1.4,
        ytd_net_return=6.2,
        long_exposure=98,
        short_exposure=-58,
        gross_exposure=156,
        net_exposure=40,
        top_sector="Information Technology",
        top_region="North America",
        liquidity_30d=84,
    ),
    # A report with an out-of-range value (net_exposure implausibly high)
    # to demonstrate the validation step routing something to review.
    dict(
        fund_name="Shenandoah Macro Fund",
        reporting_date="July 31, 2026",
        capital_base=210_000_000,
        monthly_net_return=-0.8,
        ytd_net_return=3.1,
        long_exposure=130,
        short_exposure=-40,
        gross_exposure=170,
        net_exposure=390,  # implausible vs. gross exposure -- should fail validation
        top_sector="Financials",
        top_region="Europe",
        liquidity_30d=71,
    ),
]


def generate():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, data in enumerate(SAMPLES, start=1):
        filepath = OUTPUT_DIR / f"sample_risk_report_{i}.pdf"
        _build_report(filepath, data)
        paths.append(filepath)
    return paths


if __name__ == "__main__":
    for p in generate():
        print(f"generated {p}")
