"""Outbound email via Resend HTTP API.

Used by:
- POST /admin/users/invite             → kind="invite"
- POST /admin/users/{id}/resend-invite → kind="reset"

Sign-up is invite-only — admins (and super_admin) create accounts; the
user changes the password from their profile via /auth/change-password
after first sign-in.
"""

from __future__ import annotations

from typing import Literal

import httpx

from app.config import get_settings
from app.utils.logging import get_logger

logger = get_logger(__name__)

_RESEND_URL = "https://api.resend.com/emails"

EmailKind = Literal["invite", "reset"]


# ---------------------------------------------------------------------------
# Copy variants
# ---------------------------------------------------------------------------

def _copy(kind: EmailKind, *, inviter_name: str | None) -> dict[str, str]:
    """Return wording variants for the email kind.

    Kept small and explicit so a future locale switch is a one-file change.
    """
    by = f" by {inviter_name}" if inviter_name else ""
    if kind == "reset":
        return {
            "subject": "Your Wellsynth password has been reset",
            "preheader": "A new temporary password is below. Please change it after signing in.",
            "headline": "Your password was reset",
            "lead": (
                f"An administrator{by} has reset the password on your "
                f"Wellsynth account. Use the temporary password below to sign in, "
                f"then change it from your profile."
            ),
            "cta_label": "Sign in to Wellsynth",
            "tail": (
                "If you did not expect this reset, contact your administrator "
                "immediately and change your password as soon as you sign in."
            ),
        }
    # default: invite
    return {
        "subject": "Welcome to Wellsynth — your account is ready",
        "preheader": "Your account is ready. Use the temporary password below to sign in.",
        "headline": "Welcome to Wellsynth",
        "lead": (
            f"You have been invited{by} to join Wellsynth, the well-operations "
            f"intelligence workspace. Your account is ready — use the temporary "
            f"password below to sign in."
        ),
        "cta_label": "Sign in to Wellsynth",
        "tail": (
            "For security, please change your password from your profile right "
            "after your first sign-in."
        ),
    }


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def _render_text(
    *,
    greeting: str,
    to_email: str,
    password: str,
    sign_in_url: str | None,
    copy: dict[str, str],
    from_name: str,
) -> str:
    parts: list[str] = [
        greeting,
        "",
        copy["headline"],
        "-" * len(copy["headline"]),
        "",
        copy["lead"],
        "",
        "Email:    " + to_email,
        "Password: " + password,
        "",
    ]
    if sign_in_url:
        parts.extend([copy["cta_label"] + ":", sign_in_url, ""])
    parts.extend([copy["tail"], "", "— " + from_name])
    return "\n".join(parts)


def _render_html(
    *,
    greeting: str,
    to_email: str,
    password: str,
    sign_in_url: str | None,
    copy: dict[str, str],
    from_name: str,
) -> str:
    """Email-client-safe HTML.

    Uses inline styles + table layout (tolerated by Outlook, Gmail, Apple
    Mail). No external CSS, no web fonts, no <style> blocks — those get
    stripped or rewritten by many clients.
    """
    cta = ""
    if sign_in_url:
        cta = f"""
        <tr>
          <td align="center" style="padding:24px 0 8px 0;">
            <a href="{sign_in_url}"
               style="display:inline-block;background:#0f172a;color:#ffffff;
                      text-decoration:none;font-weight:600;font-size:14px;
                      padding:12px 22px;border-radius:6px;
                      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
              {copy['cta_label']}
            </a>
          </td>
        </tr>
        """

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{copy['subject']}</title>
</head>
<body style="margin:0;padding:0;background:#f4f5f7;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
             color:#0f172a;-webkit-font-smoothing:antialiased;">
  <!-- Preheader (hidden in body, visible in inbox preview) -->
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;
              mso-hide:all;font-size:1px;line-height:1px;color:#f4f5f7;">
    {copy['preheader']}
  </div>

  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
         style="background:#f4f5f7;padding:32px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="560" cellspacing="0" cellpadding="0" border="0"
               style="max-width:560px;background:#ffffff;border-radius:10px;
                      box-shadow:0 1px 2px rgba(15,23,42,0.06);overflow:hidden;">

          <!-- Header -->
          <tr>
            <td style="background:#0f172a;padding:18px 28px;">
              <span style="color:#ffffff;font-weight:700;font-size:18px;
                           letter-spacing:0.2px;">
                {from_name}
              </span>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:28px;">
              <p style="margin:0 0 18px 0;font-size:15px;color:#475569;">
                {greeting}
              </p>

              <h1 style="margin:0 0 14px 0;font-size:22px;line-height:1.3;
                         color:#0f172a;font-weight:700;">
                {copy['headline']}
              </h1>

              <p style="margin:0 0 18px 0;font-size:15px;line-height:1.55;
                        color:#334155;">
                {copy['lead']}
              </p>

              <!-- Credentials block -->
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                     border="0" style="margin:8px 0 8px 0;">
                <tr>
                  <td style="background:#f1f5f9;border:1px solid #e2e8f0;
                             border-radius:8px;padding:16px 18px;">
                    <table role="presentation" width="100%" cellspacing="0"
                           cellpadding="0" border="0">
                      <tr>
                        <td style="font-size:12px;color:#64748b;
                                   text-transform:uppercase;letter-spacing:0.5px;
                                   padding-bottom:4px;">
                          Email
                        </td>
                      </tr>
                      <tr>
                        <td style="font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;
                                   font-size:14px;color:#0f172a;padding-bottom:14px;
                                   word-break:break-all;">
                          {to_email}
                        </td>
                      </tr>
                      <tr>
                        <td style="font-size:12px;color:#64748b;
                                   text-transform:uppercase;letter-spacing:0.5px;
                                   padding-bottom:4px;">
                          Temporary password
                        </td>
                      </tr>
                      <tr>
                        <td style="font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;
                                   font-size:15px;color:#0f172a;font-weight:600;
                                   word-break:break-all;">
                          {password}
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>

              {cta}

              <p style="margin:18px 0 0 0;font-size:13px;line-height:1.5;
                        color:#64748b;">
                {copy['tail']}
              </p>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background:#fafafa;padding:14px 28px;border-top:1px solid #eef2f7;">
              <p style="margin:0;font-size:12px;color:#94a3b8;line-height:1.5;">
                This is an automated message from {from_name}. Do not reply to
                this email — replies are not monitored. If you did not expect
                this message, please ignore it.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------

async def send_password_email(
    *,
    to_email: str,
    password: str,
    display_name: str | None,
    kind: EmailKind = "invite",
    inviter_name: str | None = None,
    sign_in_url: str | None = None,
) -> None:
    """Send a transactional email containing a temporary password.

    Raises RuntimeError if RESEND_API_KEY is not configured or the API
    returns a non-2xx response. The caller is responsible for rolling
    back any auth-side state on failure (see admin_users.invite).
    """
    settings = get_settings()
    if not settings.resend_api_key:
        raise RuntimeError("RESEND_API_KEY is not set; cannot send invite email")

    from_name = settings.resend_from_name or "Wellsynth"
    greeting = f"Hi {display_name}," if display_name else "Hi there,"
    copy = _copy(kind, inviter_name=inviter_name)
    effective_sign_in_url = (
        sign_in_url or getattr(settings, "sign_in_url", None) or None
    )

    text_body = _render_text(
        greeting=greeting,
        to_email=to_email,
        password=password,
        sign_in_url=effective_sign_in_url,
        copy=copy,
        from_name=from_name,
    )
    html_body = _render_html(
        greeting=greeting,
        to_email=to_email,
        password=password,
        sign_in_url=effective_sign_in_url,
        copy=copy,
        from_name=from_name,
    )

    payload = {
        "from": f"{from_name} <{settings.resend_from_email}>",
        "to": [to_email],
        "subject": copy["subject"],
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
            kind=kind,
        )
        raise RuntimeError(
            f"resend send failed status={resp.status_code} body={resp.text[:200]}"
        )
    logger.info("invite_email_sent", to=to_email, kind=kind)
