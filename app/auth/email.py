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


# ---------------------------------------------------------------------------
# Support tickets — outbound notification to the support inbox
# ---------------------------------------------------------------------------

import json as _json  # local alias; keep top-of-file imports minimal


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\"", "&quot;")
    )


async def send_support_ticket_email(
    *,
    ticket_id: str,
    category: str,
    subject: str,
    message: str,
    user_email: str,
    user_display_name: str | None,
    diagnostics: dict | None,
    attachment_signed_urls: list[dict],
) -> str | None:
    """Mail a newly-submitted support ticket to the support inbox.

    Returns the upstream message_id when Resend reports one; ``None`` if
    the API didn't surface one. Raises RuntimeError on transport failure
    so the caller can record ``email_sent=false`` and surface 502.
    """
    settings = get_settings()
    if not settings.resend_api_key:
        raise RuntimeError("RESEND_API_KEY is not set; cannot send support email")

    from_name = settings.resend_from_name or "Wellsynth"
    from_email = settings.support_from_email or settings.resend_from_email
    to_email = settings.support_inbox_email
    short_id = ticket_id.split("-", 1)[0]

    email_subject = f"[Wellsynth · {category}] {subject} (#{short_id})"
    user_name = user_display_name or user_email

    # ---- text body ----
    text_lines = [
        f"New support ticket #{short_id}",
        "",
        f"From:     {user_name} <{user_email}>",
        f"Category: {category}",
        f"Subject:  {subject}",
        f"Ticket:   {ticket_id}",
        "",
        "Message:",
        "--------",
        message,
        "",
    ]
    if attachment_signed_urls:
        text_lines.append("Attachments (links expire):")
        for a in attachment_signed_urls:
            text_lines.append(f"  - {a['filename']} ({a['size_bytes']} bytes) {a['url']}")
        text_lines.append("")
    if diagnostics:
        text_lines.append("Diagnostics:")
        text_lines.append(_json.dumps(diagnostics, indent=2, default=str))
        text_lines.append("")
    text_body = "\n".join(text_lines)

    # ---- html body ----
    att_html = ""
    if attachment_signed_urls:
        items = "".join(
            f"<li><a href=\"{_escape(a['url'])}\">{_escape(a['filename'])}</a> "
            f"<span style=\"color:#64748b;font-size:12px;\">({a['size_bytes']} bytes)</span></li>"
            for a in attachment_signed_urls
        )
        att_html = (
            f"<h3 style=\"margin:18px 0 6px 0;font-size:14px;color:#0f172a;\">"
            f"Attachments</h3>"
            f"<ul style=\"margin:0 0 8px 0;padding-left:18px;font-size:13px;"
            f"color:#334155;\">{items}</ul>"
            f"<p style=\"margin:0 0 12px 0;font-size:11px;color:#94a3b8;\">"
            f"Links expire in {settings.support_attachment_ttl_seconds // 3600}h.</p>"
        )

    diag_html = ""
    if diagnostics:
        diag_text = _escape(_json.dumps(diagnostics, indent=2, default=str))
        diag_html = (
            f"<h3 style=\"margin:18px 0 6px 0;font-size:14px;color:#0f172a;\">"
            f"Diagnostics</h3>"
            f"<pre style=\"background:#f1f5f9;border:1px solid #e2e8f0;"
            f"border-radius:6px;padding:12px;font-size:12px;color:#0f172a;"
            f"white-space:pre-wrap;word-break:break-all;\">{diag_text}</pre>"
        )

    html_body = f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:#f4f5f7;
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
color:#0f172a;">
  <div style="max-width:640px;margin:0 auto;background:#ffffff;
              border-radius:10px;padding:28px;
              box-shadow:0 1px 2px rgba(15,23,42,0.06);">
    <p style="margin:0 0 6px 0;font-size:12px;color:#64748b;
              text-transform:uppercase;letter-spacing:0.5px;">
      Support ticket · {_escape(category)}
    </p>
    <h1 style="margin:0 0 14px 0;font-size:20px;line-height:1.3;">
      {_escape(subject)}
    </h1>
    <p style="margin:0 0 4px 0;font-size:13px;color:#334155;">
      <strong>From:</strong> {_escape(user_name)} &lt;{_escape(user_email)}&gt;
    </p>
    <p style="margin:0 0 18px 0;font-size:13px;color:#94a3b8;">
      Ticket <code>{_escape(ticket_id)}</code>
    </p>
    <h3 style="margin:0 0 6px 0;font-size:14px;color:#0f172a;">Message</h3>
    <pre style="margin:0 0 12px 0;background:#f8fafc;border:1px solid #e2e8f0;
                border-radius:6px;padding:14px;font-size:13px;
                font-family:inherit;white-space:pre-wrap;color:#0f172a;">
{_escape(message)}</pre>
    {att_html}
    {diag_html}
  </div>
</body></html>"""

    payload = {
        "from": f"{from_name} <{from_email}>",
        "to": [to_email],
        "reply_to": [user_email],
        "subject": email_subject,
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
            "support_email_send_failed",
            status=resp.status_code,
            body=resp.text[:500],
            ticket_id=ticket_id,
        )
        raise RuntimeError(
            f"resend send failed status={resp.status_code} body={resp.text[:200]}"
        )
    try:
        data = resp.json()
        message_id = data.get("id") if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        message_id = None
    logger.info("support_email_sent", to=to_email, ticket_id=ticket_id)
    return message_id


async def send_support_ticket_update_email(
    *,
    ticket_id: str,
    subject: str,
    user_email: str,
    user_display_name: str | None,
    new_status: str | None,
    note_text: str | None,
) -> str | None:
    """Notify a user that their support ticket was updated by an admin.

    Sent only when ``settings.support_notify_user_on_update`` is true and at
    least one of ``new_status`` / ``note_text`` is present. Returns the
    Resend message id or ``None``. Raises RuntimeError on transport failure
    so the caller can record the result against the patch operation.
    """
    settings = get_settings()
    if not settings.resend_api_key:
        raise RuntimeError("RESEND_API_KEY is not set; cannot send update email")

    from_name = settings.resend_from_name or "Wellsynth"
    from_email = settings.support_from_email or settings.resend_from_email
    short_id = ticket_id.split("-", 1)[0]
    user_name = user_display_name or user_email
    email_subject = f"[Wellsynth Support] Update on your ticket #{short_id}"

    link = ""
    if settings.frontend_base_url:
        base = settings.frontend_base_url.rstrip("/")
        link = f"{base}/help/tickets/{ticket_id}"

    text_lines = [
        f"Hi {user_name},",
        "",
        f"Your support ticket \"{subject}\" (#{short_id}) was just updated.",
        "",
    ]
    if new_status:
        text_lines.append(f"New status: {new_status}")
    if note_text:
        text_lines.extend(["", "Latest note from support:", "------------------------", note_text])
    if link:
        text_lines.extend(["", f"View the ticket: {link}"])
    text_lines.extend(["", f"— {from_name}"])
    text_body = "\n".join(text_lines)

    status_html = (
        f"<p style=\"margin:0 0 12px 0;font-size:14px;color:#0f172a;\">"
        f"<strong>New status:</strong> {_escape(new_status)}</p>"
        if new_status else ""
    )
    note_html = (
        f"<h3 style=\"margin:18px 0 6px 0;font-size:14px;color:#0f172a;\">"
        f"Latest note from support</h3>"
        f"<pre style=\"margin:0 0 12px 0;background:#f8fafc;border:1px solid #e2e8f0;"
        f"border-radius:6px;padding:14px;font-size:13px;font-family:inherit;"
        f"white-space:pre-wrap;color:#0f172a;\">{_escape(note_text)}</pre>"
        if note_text else ""
    )
    cta_html = (
        f"<p style=\"margin:18px 0 0 0;\">"
        f"<a href=\"{_escape(link)}\" "
        f"style=\"display:inline-block;background:#0f172a;color:#ffffff;"
        f"text-decoration:none;font-weight:600;font-size:14px;"
        f"padding:10px 18px;border-radius:6px;\">Open ticket</a></p>"
        if link else ""
    )

    html_body = f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:#f4f5f7;
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
color:#0f172a;">
  <div style="max-width:560px;margin:0 auto;background:#ffffff;
              border-radius:10px;padding:28px;
              box-shadow:0 1px 2px rgba(15,23,42,0.06);">
    <p style="margin:0 0 6px 0;font-size:12px;color:#64748b;
              text-transform:uppercase;letter-spacing:0.5px;">Support update</p>
    <h1 style="margin:0 0 14px 0;font-size:20px;line-height:1.3;">
      {_escape(subject)}
    </h1>
    <p style="margin:0 0 12px 0;font-size:13px;color:#94a3b8;">
      Ticket <code>#{_escape(short_id)}</code>
    </p>
    {status_html}
    {note_html}
    {cta_html}
  </div>
</body></html>"""

    payload = {
        "from": f"{from_name} <{from_email}>",
        "to": [user_email],
        "subject": email_subject,
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
            "support_update_email_send_failed",
            status=resp.status_code, body=resp.text[:500], ticket_id=ticket_id,
        )
        raise RuntimeError(
            f"resend send failed status={resp.status_code} body={resp.text[:200]}"
        )
    try:
        data = resp.json()
        message_id = data.get("id") if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        message_id = None
    logger.info("support_update_email_sent", to=user_email, ticket_id=ticket_id)
    return message_id
