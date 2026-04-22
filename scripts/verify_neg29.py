"""Verify -29 shift decode on the page 0/1/2 garbled text."""
samples = [
    ("dpt^", "GSWA"),
    ("e^osbv", "HARVEY"),
    ("tbii", "WELL"),
    ("`ljmibqflk", "COMPLETION"),
    ("mobifjfk^ov", "PRELIMINARY"),
    ("fkqbomobq^qflk", "INTERPRETATION"),
    ("obmloq", "REPORT"),
    ("plrqebok", "SOUTHERN"),
    ("mboqe", "PERTH"),
    ("_^pfk", "BASIN"),
    ("afob`qlo", "DIRECTOR"),
    ("dbkbo^i", "GENERAL"),
    ("abm^oqjbkq", "DEPARTMENT"),
    ("jfkbp", "MINES"),
    ("mbqolibrj", "PETROLEUM"),
    ("bub`rqfsb", "EXECUTIVE"),
    ("dblildf`^i", "GEOLOGICAL"),
    ("prosbv", "SURVEY"),
    ("tbpqbok", "WESTERN"),
    ("^rpqo^if^", "AUSTRALIA"),
    ("obcbobk`b", "REFERENCE"),
]

print("Testing -29 shift:")
for garbled, expected in samples:
    decoded = "".join(chr(ord(c) - 29) for c in garbled)
    match = "✓" if decoded.upper() == expected.upper() else "✗"
    print(f"  {match} {garbled:25s} -> {decoded:25s} (expected: {expected})")
