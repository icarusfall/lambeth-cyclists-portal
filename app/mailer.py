"""Newsletter sending via Resend (same service the email processor uses)."""

import logging

import markdown as md
import resend

from app.config import get_settings

logger = logging.getLogger(__name__)


def markdown_to_email_html(markdown_body: str) -> str:
    """Render newsletter markdown into a simple, phone-friendly HTML email."""
    body_html = md.markdown(markdown_body, extensions=["extra"])
    return f"""\
<!doctype html>
<html>
<body style="margin:0;padding:0;background:#f4f6f4;">
  <div style="max-width:600px;margin:0 auto;padding:16px;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#222;font-size:16px;line-height:1.55;">
    <div style="background:#1a7a3c;color:#fff;padding:14px 18px;border-radius:8px 8px 0 0;">
      <strong style="font-size:18px;">Lambeth Cyclists</strong>
    </div>
    <div style="background:#ffffff;padding:18px;border-radius:0 0 8px 8px;">
      {body_html}
    </div>
    <p style="color:#777;font-size:13px;padding:12px 4px;">
      Lambeth Cyclists is the Lambeth branch of the London Cycling Campaign.
    </p>
  </div>
</body>
</html>
"""


def send_newsletter(subject: str, markdown_body: str, to_email: str) -> str:
    """Send the newsletter. Returns the Resend email id. Raises on failure."""
    settings = get_settings()
    resend.api_key = settings.resend_api_key
    response = resend.Emails.send(
        {
            "from": settings.newsletter_from,
            "to": [to_email],
            "subject": subject,
            "text": markdown_body,
            "html": markdown_to_email_html(markdown_body),
        }
    )
    email_id = response.get("id", "unknown")
    logger.info("Newsletter sent to %s (resend id %s)", to_email, email_id)
    return email_id
