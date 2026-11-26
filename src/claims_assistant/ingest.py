"""Unstructured document ingestion → structured claim JSON.

Pipeline: extract text (PDF/DOCX/XLSX/CSV/TXT) → LLM extraction agent with a
validate-and-repair loop → normalized claim persisted to the claims store.

The agent does the structuring; validation is deterministic and every attempt is
logged, so an extraction can be audited like any other agent run.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from . import config
from .llm import _redact_pii
from .prompts import prompt_text
from .tracing import record_run

MAX_CHARS = 20_000
ALLOWED_TYPES = {"auto", "home", "health"}
ALLOWED_REGIONS = {"CA", "TX", "FL", "NY"}
REGION_NAMES = {
    "california": "CA", "texas": "TX", "florida": "FL", "new york": "NY",
    "ca": "CA", "tx": "TX", "fl": "FL", "ny": "NY",
}
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y",
                "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class ScannedDocumentError(ValueError):
    """No text layer (scanned PDF) or an image upload — needs vision OCR."""


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------
def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_text(filename: str, data: bytes) -> dict:
    """Extract plain text from a supported document. Raises ValueError otherwise."""
    suffix = Path(filename).suffix.lower()
    notes = ""

    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        notes = f"{len(reader.pages)} page(s)"
        if len(text.strip()) < 40:
            raise ScannedDocumentError("PDF has no text layer — using vision OCR")
    elif suffix in IMAGE_SUFFIXES:
        raise ScannedDocumentError("image document — using vision OCR")
    elif suffix in {".docx", ".doc"}:
        if suffix == ".doc":
            raise ValueError("legacy .doc is not supported — save as .docx")
        import docx
        doc = docx.Document(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        text = "\n".join(p for p in parts if p.strip())
        notes = f"{len(doc.paragraphs)} paragraph(s)"
    elif suffix == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        lines: list[str] = []
        for ws in wb.worksheets:
            lines.append(f"# sheet: {ws.title}")
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) for c in row if c not in (None, "")]
                if cells:
                    lines.append(" | ".join(cells))
        text = "\n".join(lines)
        notes = f"{len(wb.worksheets)} sheet(s)"
    elif suffix == ".csv":
        rows = list(csv.reader(io.StringIO(_decode(data))))
        text = "\n".join(" | ".join(r) for r in rows if any(c.strip() for c in r))
        notes = f"{len(rows)} row(s)"
    elif suffix in {".txt", ".md"}:
        text = _decode(data)
        notes = "plain text"
    else:
        raise ValueError(f"unsupported file type '{suffix}' — use pdf, docx, xlsx, csv, txt, png, jpg")

    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise ValueError("no extractable text found (scanned PDFs need OCR)")
    return {"format": suffix.lstrip("."), "text": text[:MAX_CHARS],
            "chars": len(text), "notes": notes}


def _normalize_image(data: bytes, max_width: int = 1600) -> bytes:
    """Decode, flatten to RGB, downscale, re-encode as PNG for the vision API."""
    from PIL import Image

    img = Image.open(io.BytesIO(data)).convert("RGB")
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_document_images(filename: str, data: bytes,
                           max_pages: int = 3) -> list[bytes]:
    """Render a scanned PDF (or pass through an image) as PNGs for OCR."""
    suffix = Path(filename).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return [_normalize_image(data)]

    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    images = []
    for page in list(doc)[:max_pages]:
        pix = page.get_pixmap(dpi=150)
        images.append(_normalize_image(pix.tobytes("png")))
    if not images:
        raise ValueError("could not render any pages from the PDF")
    return images


# --------------------------------------------------------------------------
# Validation / normalization
# --------------------------------------------------------------------------
def _parse_date(value) -> str | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_claim(raw: dict) -> tuple[dict, list[str]]:
    """Normalize model output; return (claim, issues). Issues block persistence."""
    issues: list[str] = []
    claim: dict = {}

    ctype = str(raw.get("claim_type") or "").strip().lower()
    if ctype not in ALLOWED_TYPES:
        issues.append(f"claim_type '{raw.get('claim_type')}' not in {sorted(ALLOWED_TYPES)}")
    claim["claim_type"] = ctype

    region_raw = str(raw.get("region") or "").strip().lower()
    region = REGION_NAMES.get(region_raw, region_raw.upper() if len(region_raw) == 2 else "")
    if region not in ALLOWED_REGIONS:
        issues.append(f"region '{raw.get('region')}' not in {sorted(ALLOWED_REGIONS)}")
    claim["region"] = region

    incident = _parse_date(raw.get("incident_date"))
    if not incident:
        issues.append("incident_date missing or not a recognisable date")
    claim["incident_date"] = incident

    reported = _parse_date(raw.get("reported_date")) or incident
    claim["reported_date"] = reported

    try:
        amount = float(str(raw.get("claimed_amount") or 0).replace(",", "").replace("$", ""))
    except ValueError:
        amount = 0.0
    if amount <= 0:
        issues.append("claimed_amount missing or not positive")
    claim["claimed_amount"] = round(amount, 2)

    verbatim = str(raw.get("verbatim") or "").strip()
    if len(verbatim) < 20:
        issues.append("verbatim missing or too short to triage")
    claim["verbatim"] = verbatim

    claim["claimant"] = raw.get("claimant") or None
    claim["policy_id"] = raw.get("policy_id") or None
    claim["channel"] = raw.get("channel") or "upload"
    status = str(raw.get("status") or "open").lower()
    claim["status"] = status if status in {"open", "under_review", "closed", "denied"} else "open"
    return claim, issues


def _parse_json(raw: str) -> dict | None:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)
        if match:
            try:
                value = json.loads(match.group(0))
                return value if isinstance(value, dict) else None
            except json.JSONDecodeError:
                return None
    return None


# --------------------------------------------------------------------------
# Ingestion agent
# --------------------------------------------------------------------------
class IngestionAgent:
    """Extract → validate → repair loop (max attempts), with an auditable log."""

    def __init__(self, llm, max_attempts: int = 3):
        self.llm = llm
        self.max_attempts = max_attempts

    def extract(self, filename: str, text: str = "",
                images: list[bytes] | None = None) -> dict:
        if images:
            safe_text = "[scanned document supplied as images — read the text from the pages]"
        else:
            safe_text = _redact_pii(text)
        base_prompt = (prompt_text("ingest_claim")
                       .replace("{filename}", filename)
                       .replace("{document_text}", safe_text))
        user = base_prompt
        log: list[dict] = []
        attempts = 0
        claim: dict | None = None
        issues: list[str] = []
        last_raw = ""

        while attempts < self.max_attempts:
            attempts += 1
            if images:
                raw = self.llm.chat_vision(
                    "You are a precise insurance document-extraction agent.",
                    user, images, kind="ingest_vision")
            else:
                raw = self.llm.chat(
                    "You are a precise insurance document-extraction agent.",
                    user, json_mode=True, kind="ingest")
            last_raw = raw
            parsed = _parse_json(raw)
            if parsed is None:
                issues = ["response was not valid JSON"]
                claim = None
            else:
                claim, issues = normalize_claim(parsed)
            log.append({"tool": f"llm_extract", "args": {"attempt": attempts},
                        "ok": not issues})
            if not issues:
                break
            user = (base_prompt
                    + "\n\nYour previous answer had these problems:\n- "
                    + "\n- ".join(issues)
                    + "\nReturn ONLY corrected JSON.")

        if claim is None or issues:
            claim = claim or {}
            claim.update({
                "claim_type": claim.get("claim_type") or "auto",
                "region": claim.get("region") or "",
                "status": "needs_review",
                "claimed_amount": float(claim.get("claimed_amount") or 0),
                "incident_date": claim.get("incident_date") or None,
                "reported_date": claim.get("reported_date") or None,
                "verbatim": (claim.get("verbatim") or safe_text[:2000]).strip(),
                "claimant": claim.get("claimant"),
                "policy_id": claim.get("policy_id"),
                "channel": "upload",
            })
            status = "needs_review"
        else:
            status = claim.get("status", "open")

        return {"claim": claim, "status": status, "issues": issues,
                "attempts": attempts, "log": log, "raw_last": last_raw}


def _norm_name(value: str) -> str:
    return re.sub(r"[^a-z ]", "", str(value or "").lower()).strip()


def find_duplicate(claim: dict, claims: list[dict],
                   amount_tolerance: float = 0.05,
                   date_window_days: int = 7) -> str | None:
    """Detect a likely duplicate: same type+region, close amount and date, same claimant."""
    amount = float(claim.get("claimed_amount") or 0)
    for other in claims:
        if (other.get("claim_type") != claim.get("claim_type")
                or other.get("region") != claim.get("region")):
            continue
        other_amount = float(other.get("claimed_amount") or 0)
        if amount <= 0 or other_amount <= 0:
            continue
        if abs(other_amount - amount) / max(other_amount, amount) > amount_tolerance:
            continue

        dates = (other.get("incident_date"), claim.get("incident_date"))
        if all(dates):
            try:
                gap = abs((date.fromisoformat(dates[0]) - date.fromisoformat(dates[1])).days)
            except ValueError:
                continue
            if gap > date_window_days:
                continue
        else:
            continue

        name_a, name_b = _norm_name(other.get("claimant", "")), _norm_name(claim.get("claimant", ""))
        if name_a and name_b and name_a != name_b:
            continue
        return other["claim_id"]
    return None


def ingest_document(filename: str, data: bytes, llm, db, max_attempts: int = 3) -> dict:
    """Full pipeline: extract text (or OCR), run the agent, persist the claim."""
    images: list[bytes] | None = None
    try:
        extracted = extract_text(filename, data)
    except ScannedDocumentError:
        images = render_document_images(filename, data)
        extracted = {
            "format": Path(filename).suffix.lstrip("."),
            "text": "",
            "chars": 0,
            "notes": f"vision OCR · {len(images)} page(s)",
        }

    agent = IngestionAgent(llm, max_attempts=max_attempts)
    result = agent.extract(filename, extracted["text"], images=images)

    claim = result["claim"]
    duplicate = find_duplicate(claim, db.list())
    if duplicate:
        claim["duplicate_of"] = duplicate
        claim["status"] = "needs_review"
        result["status"] = "needs_review"
        result["issues"].append(f"possible duplicate of {duplicate}")
        result["log"].append({"tool": "duplicate_check", "args": {}, "ok": False})
    else:
        result["log"].append({"tool": "duplicate_check", "args": {}, "ok": True})

    claim["claim_id"] = db.next_claim_id()
    claim["source"] = {
        "filename": filename,
        "format": extracted["format"],
        "chars": extracted["chars"],
        "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    db.add(claim)

    uploads_dir = config.UPLOADS_DIR
    uploads_dir.mkdir(parents=True, exist_ok=True)
    (uploads_dir / f"{claim['claim_id']}.json").write_text(
        json.dumps({"extraction": {k: v for k, v in extracted.items() if k != "text"},
                    "mode": "vision" if images else "text",
                    "attempts": result["attempts"],
                    "issues": result["issues"],
                    "claim": claim}, indent=2),
        encoding="utf-8")

    log = [{"tool": "extract_text",
            "args": {"filename": filename, "format": extracted["format"]},
            "ok": not images}] + result["log"]
    run = record_run("ingest", [claim["claim_id"]], log,
                     f"{claim['claim_id']} | {result['status']} | "
                     f"{result['attempts']} attempt(s) | "
                     f"{'vision' if images else 'text'}",
                     model=getattr(llm, "model", None))

    return {
        "claim": claim,
        "extraction": {"format": extracted["format"], "chars": extracted["chars"],
                       "notes": extracted["notes"], "attempts": result["attempts"],
                       "issues": result["issues"], "status": result["status"],
                       "mode": "vision" if images else "text",
                       "duplicate_of": claim.get("duplicate_of")},
        "run_id": run["run_id"],
    }
