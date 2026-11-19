"""Document ingestion: extractors, validation, agent repair loop, persistence."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claims_assistant import config  # noqa: E402
from claims_assistant.ingest import (IngestionAgent, ScannedDocumentError,  # noqa: E402
                                     extract_text, find_duplicate,
                                     ingest_document, normalize_claim,
                                     render_document_images)
from claims_assistant.services import ClaimsDB  # noqa: E402
from conftest import FakeLLM  # noqa: E402

GOOD = {
    "claim_type": "auto", "region": "CA", "status": "open",
    "claimant": "Marcus Reyes", "policy_id": "POL-A-1",
    "incident_date": "2026-09-12", "reported_date": "2026-09-13",
    "claimed_amount": 8420, "channel": "upload",
    "verbatim": "A vehicle was struck from behind on the highway. Photos and a police report are attached.",
}
TEXT = "Auto claim report. Policy POL-A-1. Incident 2026-09-12 in California. Amount $8,420."


# ---------------------------------------------------------------- extractors
def test_extract_txt():
    out = extract_text("claim.txt", TEXT.encode())
    assert out["format"] == "txt" and "POL-A-1" in out["text"]


def test_extract_csv():
    data = b"field,value\nclaim_type,auto\nregion,CA\n"
    out = extract_text("claim.csv", data)
    assert "claim_type" in out["text"]


def test_extract_docx():
    import docx

    doc = docx.Document()
    doc.add_paragraph("Home claim in Texas. Wind damage to the roof.")
    buf = io.BytesIO()
    doc.save(buf)
    out = extract_text("claim.docx", buf.getvalue())
    assert "Texas" in out["text"]


def test_extract_xlsx():
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["claim_type", "health"])
    ws.append(["region", "NY"])
    buf = io.BytesIO()
    wb.save(buf)
    out = extract_text("claim.xlsx", buf.getvalue())
    assert "health" in out["text"] and "NY" in out["text"]


def test_extract_pdf():
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 8, TEXT)
    out = extract_text("claim.pdf", bytes(pdf.output()))
    assert "Auto claim" in out["text"]


def test_rejects_unsupported_type():
    with pytest.raises(ValueError):
        extract_text("claim.exe", b"MZ")


def test_rejects_empty_document():
    with pytest.raises(ValueError):
        extract_text("blank.txt", b"   \n\n  ")


# ---------------------------------------------------------------- validation
def test_normalize_valid():
    claim, issues = normalize_claim(GOOD)
    assert not issues
    assert claim["region"] == "CA" and claim["claimed_amount"] == 8420.0


def test_normalize_state_names_and_dates():
    raw = dict(GOOD, region="California", incident_date="12/09/2026")
    claim, issues = normalize_claim(raw)
    assert claim["region"] == "CA"
    assert claim["incident_date"] == "2026-09-12"
    assert not issues


def test_normalize_rejects_bad_fields():
    raw = dict(GOOD, claim_type="boat", region="ZZ", claimed_amount=0,
               incident_date="not-a-date", verbatim="short")
    _, issues = normalize_claim(raw)
    assert len(issues) >= 4


# ---------------------------------------------------------------- agent loop
def test_agent_repairs_invalid_output():
    bad = dict(GOOD, claim_type="boat")
    fake = FakeLLM(chat_script=[json.dumps(bad), json.dumps(GOOD)])
    result = IngestionAgent(fake).extract("claim.txt", TEXT)
    assert result["attempts"] == 2
    assert not result["issues"]
    assert result["claim"]["claim_type"] == "auto"
    assert [entry["ok"] for entry in result["log"]] == [False, True]


def test_agent_falls_back_to_needs_review():
    bad = dict(GOOD, region="ZZ")
    fake = FakeLLM(chat_script=[json.dumps(bad)] * 3)
    result = IngestionAgent(fake).extract("claim.txt", TEXT)
    assert result["attempts"] == 3
    assert result["status"] == "needs_review"
    assert result["claim"]["status"] == "needs_review"
    assert result["claim"]["verbatim"]


def test_agent_handles_non_json():
    fake = FakeLLM(chat_script=["not json at all"] * 3)
    result = IngestionAgent(fake).extract("claim.txt", TEXT)
    assert result["status"] == "needs_review"
    assert any("not valid JSON" in i for i in result["issues"])


# ---------------------------------------------------------------- persistence
def test_ingest_document_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNS_PATH", tmp_path / "runs.jsonl")
    claims_file = tmp_path / "claims.json"
    claims_file.write_text("[]", encoding="utf-8")
    db = ClaimsDB(path=claims_file)

    fake = FakeLLM(chat_script=[json.dumps(GOOD)])
    result = ingest_document("claim.txt", TEXT.encode(), fake, db)

    claim = result["claim"]
    assert claim["claim_id"] == "CLM-2001"
    assert claim["source"]["format"] == "txt"
    assert db.get("CLM-2001")["verbatim"].startswith("A vehicle")
    assert (tmp_path / "uploads" / "CLM-2001.json").exists()
    assert result["extraction"]["attempts"] == 1


def test_next_claim_id_increments(tmp_path):
    path = tmp_path / "claims.json"
    path.write_text(json.dumps([{"claim_id": "CLM-1046"}]), encoding="utf-8")
    db = ClaimsDB(path=path)
    assert db.next_claim_id() == "CLM-1047"


# ---------------------------------------------------------------- OCR / vision
def _png_image() -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (900, 400), "white")
    ImageDraw.Draw(img).text((30, 30), "AUTO CLAIM\nMarcus Reyes\nAmount: 8420", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _scanned_pdf() -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.image(io.BytesIO(_png_image()), x=10, y=10, w=180)
    return bytes(pdf.output())


def test_scanned_pdf_requires_ocr():
    with pytest.raises(ScannedDocumentError):
        extract_text("scan.pdf", _scanned_pdf())


def test_image_requires_ocr():
    with pytest.raises(ScannedDocumentError):
        extract_text("photo.png", _png_image())


def test_render_scanned_pages():
    images = render_document_images("scan.pdf", _scanned_pdf())
    assert images and images[0].startswith(b"\x89PNG")


def test_vision_agent_extracts():
    fake = FakeLLM(chat_script=[json.dumps(GOOD)])
    result = IngestionAgent(fake).extract("scan.pdf", "", images=[_png_image()])
    assert result["attempts"] == 1
    assert result["claim"]["claim_type"] == "auto"


def test_duplicate_matching():
    existing = {"claim_id": "CLM-1", "claim_type": "auto", "region": "CA",
                "claimed_amount": 8420.0, "incident_date": "2026-09-12",
                "claimant": "Marcus Reyes"}
    base = {"claim_type": "auto", "region": "CA", "claimed_amount": 8500.0,
            "incident_date": "2026-09-14", "claimant": "marcus reyes"}
    assert find_duplicate(base, [existing]) == "CLM-1"
    assert find_duplicate({**base, "claimed_amount": 40000.0}, [existing]) is None
    assert find_duplicate({**base, "claimant": "Someone Else"}, [existing]) is None


def test_ingest_vision_marks_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "RUNS_PATH", tmp_path / "runs.jsonl")
    claims_file = tmp_path / "claims.json"
    claims_file.write_text(json.dumps([{
        "claim_id": "CLM-1040", "claim_type": "auto", "region": "CA",
        "claimed_amount": 8420.0, "incident_date": "2026-09-12",
        "claimant": "Marcus Reyes"}]), encoding="utf-8")
    db = ClaimsDB(path=claims_file)

    fake = FakeLLM(chat_script=[json.dumps(GOOD)])
    result = ingest_document("scan.png", _png_image(), fake, db)

    assert result["extraction"]["mode"] == "vision"
    assert result["claim"]["duplicate_of"] == "CLM-1040"
    assert result["claim"]["status"] == "needs_review"
    assert any("duplicate" in issue for issue in result["extraction"]["issues"])
