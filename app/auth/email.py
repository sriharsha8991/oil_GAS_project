"""Outbound email via Resend HTTP API.

Used by POST /admin/users/invite to deliver the generated temporary
password to a newly invited user. Sign-up is invite-only — admins (and
super_admin) create accounts; the user changes the password from their
profile via POST /auth/change-password after first sign-in.
"""

from __future__ import annotations

import httpx

from app.config import get_settings
from app.utils.logging import get_logger

logger = get_logger(__name__)

_RESEND_URL = "https://api.resend.com/emails"


async def send_password_email(
    *, to_email: str, password: str, display_name: str | None
) -> None:
    """Send a transactional email containing the user's generated password.

    Raises RuntimeError if RESEND_API_KEY is not configured.
    """
    settings = get_settings()
    if not settings.resend_api_key:
        raise RuntimeError("RESEND_API_KEY is not set; cannot send invite email")

    greeting = f"Hi {display_name}," if display_name else "Hi,"
    subject = "Your Wellsynth account password"
    text_body = (
        f"{greeting}\n\n"
        "Your Wellsynth account is ready. Use the credentials below to sign in:\n\n"
        f"  Email:    {to_email}\n"
        f"  Password: {password}\n\n"
        "You can change this password from your profile after signing in.\n\n"
        "— Wellsynth"
    )
    html_body = (
        f"<p>{greeting}</p>"
        "<p>Your Wellsynth account is ready. Use the credentials below to sign in:</p>"
        "<p style=\"font-family:monospace\">"
        f"Email: {to_email}<br>"
        f"Password: <strong>{password}</strong>"
        "</p>"
        "<p>You can change this password from your profile after signing in.</p>"
        "<p>— Wellsynth</p>"
    )

    payload = {
        "from": f"{settings.resend_from_name} <{settings.resend_from_email}>",
        "to": [to_email],
        "subject": subject,
        "text": text_body,
        "html": html_body,
    }
    headers = {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(_RESEND_URL, json=payload, headers=headers)
    if resp.status_code >= 300:
        logger.error(
            "resend_send_failed",
            status=resp.status_code,
            body=resp.text[:500],
            to=to_email,
        )
        raise RuntimeError(
            f"resend send failed status={resp.status_code} body={resp.text[:200]}"
        )
    logger.info("invite_email_sent", to=to_email)
