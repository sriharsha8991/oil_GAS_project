"""Quick test of pymupdf4llm on the Harvey PDF."""
import pymupdf4llm

md = pymupdf4llm.to_markdown(
    "data/uploads/GSWA Harvey 1 WCR.pdf",
    pages=[0, 1, 6, 7, 10],  # Title, title2, garbled bullets, figure, table-heavy
    use_ocr=False,
)

print(md[:5000])
print("\n\n... (truncated) ...\n")
print(f"Total length: {len(md)} chars")
