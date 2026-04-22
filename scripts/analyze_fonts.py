"""Quick script to analyze font encodings in the test PDF."""
import fitz

doc = fitz.open("data/uploads/GSWA Harvey 1 WCR.pdf")

# Page 7 (index 6) has garbled bullet points
for page_idx in [6, 7, 10]:
    page = doc[page_idx]
    d = page.get_text("dict")
    print(f"\n{'='*80}")
    print(f"PAGE {page_idx + 1}")
    print(f"{'='*80}")
    for block in d["blocks"]:
        if block["type"] == 0:  # text block
            for line in block["lines"]:
                for span in line["spans"]:
                    text = span["text"].strip()
                    if text:
                        font = span["font"]
                        size = span["size"]
                        flags = span["flags"]
                        print(f"  FONT={font:<35s} SIZE={size:5.1f} FLAGS={flags:3d} | {text[:80]}")

doc.close()
