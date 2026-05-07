"""Tiny email shape validator (avoids the email-validator dependency).

Pydantic's `EmailStr` requires the optional `email-validator` package,
which we are not adding. This helper enforces the minimal shape we
actually care about (one '@', non-empty local + domain, has a dot in
the domain, no whitespace) and lowercases/strips the result.
"""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def validate_email_str(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("email must be a string")
    v = value.strip().lower()
    if not _EMAIL_RE.match(v):
        raise ValueError("invalid_email")
    return v
