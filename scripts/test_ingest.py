"""Re-run pipeline via API and display results."""
import json
import requests

r = requests.post(
    "http://127.0.0.1:8000/ingest",
    files={"file": ("GSWA Harvey 1 WCR.pdf", open("data/uploads/GSWA Harvey 1 WCR.pdf", "rb"), "application/pdf")},
    timeout=300,
)
d = r.json()

# Summary
summary = {k: v for k, v in d.items() if k != "pages"}
print(json.dumps(summary, indent=2))

# Per-page preview
print(f"\nPages: {len(d.get('pages', []))}")
for p in d.get("pages", []):
    preview = p["text_preview"][:120].replace("\n", " ")
    print(f"  p{p['page_num']:2d} | {p['char_count']:5d} chars | scan={str(p['is_scanned']):5s} | {preview}")
