# Sample intake documents

Use these to verify the ingestion pipeline (drag into the **Ingest a document** panel, or
`curl -F "file=@samples/<file>" http://127.0.0.1:8010/api/uploads`).

| File | Format | Path exercised | Expected result |
|---|---|---|---|
| `health-claim-intake.csv` | CSV (text) | text extraction | New claim · health/NY · $2,450 · **open** |
| `claims.xlsx` | XLSX (text) | sheet/text extraction | New claim · health/NY · $2,450 · **open** |
| `scanned-claim.pdf` | image-only PDF | **vision OCR** | New claim · home/FL · $12,400 · **open** |
| `claim-photo.png` | PNG image | **vision OCR** | New claim · auto/TX · $9,300 · **open** |
| `home-claim-intake.pdf` | text PDF | extraction + dedup | Flagged **duplicate** of an existing home/TX claim → `needs_review` |
| `auto-claim-intake.docx` | DOCX | extraction + dedup | Flagged **duplicate** of CLM-1042 → `needs_review` |

Notes:
- Scanned documents are rendered at 150 dpi and sent to the vision model; the first 3 pages
  are used.
- PII is redacted before any model call.
- `needs_review` means the validator or duplicate check blocked automatic acceptance; the
  claim is still stored and visible, never silently discarded.
- Regenerate these files with `python scripts/make_samples.py`.
