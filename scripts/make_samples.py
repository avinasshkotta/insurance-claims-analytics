"""Generate the sample intake documents used to verify ingestion.

Text documents exercise extraction; the scanned PDF and PNG exercise the
vision/OCR path; two documents intentionally duplicate existing claims to
demonstrate duplicate detection.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

import docx
import openpyxl
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "samples"
OUT.mkdir(exist_ok=True)

HOME_TEXT = """FIRST NOTICE OF LOSS - HOMEOWNERS CLAIM

Claimant: Priya Nair
Policy number: POL-H-21093
Date of loss: 30 August 2026
Date reported: 18 September 2026
Property: 4412 Oak Ridge Drive, Austin, Texas
Amount claimed: $47,800

Description of loss: Storm water entered the ground floor through a broken
window during heavy rain. The living room flooring, drywall, and furniture
were damaged. A neighbour observed the water entering the property. The
remediation company was paid in cash and no receipts were provided. The
claimant requests the full amount without an itemised estimate.
"""

AUTO_LINES = [
    "Claimant: Marcus Reyes",
    "Policy: POL-A-88421",
    "Date of loss: 2026-09-20",
    "Date reported: 2026-09-20",
    "State: California",
    "Amount claimed: $125,000",
    "Vehicle: 2022 sedan, stolen from the driveway overnight.",
    "Police report: not yet filed. Claimant needs the payout this week to buy a"
    " replacement from a cousin's dealership.",
]

HEALTH_FIELDS = ["claim_id", "claim_type", "region", "claimant", "incident_date",
                 "reported_date", "claimed_amount", "channel", "verbatim"]
HEALTH_ROWS = [
    ["", "health", "NY", "Devon Clarke", "2026-10-05", "2026-10-06", "2450",
     "provider",
     "Outpatient physiotherapy at Mount Sinai for a documented knee injury. "
     "Prior authorization was obtained and the itemised invoice is attached."],
]

SCAN_LINES = [
    "HOME CLAIM - WATER DAMAGE",
    "",
    "Claimant: Elena Marsh",
    "Policy: POL-H-77410",
    "Date of loss: 2026-10-02",
    "Date reported: 2026-10-03",
    "Property: 88 Coral Way, Miami, Florida",
    "Amount claimed: $12,400",
    "",
    "Kitchen ceiling collapsed after a burst pipe. Plumber invoice and",
    "photos are attached. No prior claims on the policy.",
]

PHOTO_LINES = [
    "AUTO CLAIM NOTE",
    "",
    "Claimant: Jordan Blake",
    "Policy: POL-A-66120",
    "Date of loss: 2026-10-10",
    "Date reported: 2026-10-11",
    "State: Texas",
    "Amount claimed: $9,300",
    "",
    "Rear-ended at a red light. Other driver exchanged insurance details.",
    "Photos of the bumper and a repair estimate are attached.",
]


def _font(size: int):
    for name in ("arial.ttf", "segoeui.ttf", "calibri.ttf"):
        path = Path("C:/Windows/Fonts") / name
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def render_text_image(lines: list[str], width: int = 1240, size: int = 26) -> bytes:
    line_height = int(size * 1.6)
    height = 40 + line_height * len(lines)
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    font = _font(size)
    y = 30
    for line in lines:
        draw.text((40, y), line, fill="black", font=font)
        y += line_height
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def write_text_pdf(path: Path, text: str) -> None:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 8, text)
    pdf.output(str(path))


def write_scanned_pdf(path: Path, lines: list[str]) -> None:
    image = render_text_image(lines)
    pdf = FPDF()
    pdf.add_page()
    pdf.image(io.BytesIO(image), x=8, y=8, w=194)
    pdf.output(str(path))


# ---- text documents -------------------------------------------------------
write_text_pdf(OUT / "home-claim-intake.pdf", HOME_TEXT)

doc = docx.Document()
doc.add_heading("Auto Claim - First Notice of Loss", 1)
for line in AUTO_LINES:
    doc.add_paragraph(line)
doc.save(OUT / "auto-claim-intake.docx")

with (OUT / "health-claim-intake.csv").open("w", newline="", encoding="utf-8") as fh:
    writer = csv.writer(fh)
    writer.writerow(HEALTH_FIELDS)
    for row in HEALTH_ROWS:
        writer.writerow(row)

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "claims"
ws.append(HEALTH_FIELDS)
for row in HEALTH_ROWS:
    ws.append(row)
wb.save(OUT / "claims.xlsx")

# ---- OCR / vision documents ----------------------------------------------
write_scanned_pdf(OUT / "scanned-claim.pdf", SCAN_LINES)
(OUT / "claim-photo.png").write_bytes(render_text_image(PHOTO_LINES))

print("samples written:")
for path in sorted(OUT.iterdir()):
    print(f"  {path.name:<26} {path.stat().st_size / 1024:>7.1f} KB")
