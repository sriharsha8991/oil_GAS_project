"""Verify ROT-31 decoding on garbled text samples."""
samples = [
    "$PNNPOXFBMUI",
    "(PWFSONFOU",
    "%FQBSUNFOU",
    "8\"4UBUF",
    "JOEVTUSZ",
    "5IFSFXBT",
    "DPOàSN",
    "QSPWJEF",
    "FOBCMF",
    "BTTJTU",
    "BATVQFSDSJUJDBMQIBTF",
]

for s in samples:
    decoded = "".join(chr(ord(c) + 31) for c in s)
    print(f"  {s:40s} -> {decoded}")
