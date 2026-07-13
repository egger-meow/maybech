"""
Email notification integration.

Uses SMTP (Gmail by default) to send trade alerts and performance reports.
Configure in .env — leave EMAIL_SENDER blank to disable.
"""

import logging
import re
import smtplib
from email.mime.text import MIMEText

from src.config.settings import settings
from src.notifications.repeat_cooldown import RepeatCooldownTracker

logger = logging.getLogger(__name__)


class EmailNotifier:
    """Sends email notifications via SMTP."""

    def __init__(self) -> None:
        self.last_error = ""
        self.suppressed_by_cooldown = False
        self._cooldown = RepeatCooldownTracker(max(0, settings.NOTIFICATION_COOLDOWN_SECONDS))
        self.enabled = bool(
            settings.EMAIL_SENDER
            and settings.EMAIL_PASSWORD
            and settings.EMAIL_RECEIVER
        )

    def send(self, subject: str, body: str) -> bool:
        """Send a plain-text email.

        Does nothing if email credentials are not configured.
        """
        self.suppressed_by_cooldown = False
        if not self.enabled:
            return False
        fingerprint = re.sub(
            r"\s+",
            " ",
            f"{subject}\n{body}".strip(),
        ).casefold()
        if not self._cooldown.ready(fingerprint):
            logger.info("Equivalent email notification suppressed by cooldown.")
            self.suppressed_by_cooldown = True
            return False
        message = MIMEText(body, _charset="utf-8")
        message["Subject"] = subject
        message["From"] = settings.EMAIL_SENDER
        message["To"] = settings.EMAIL_RECEIVER
        try:
            with smtplib.SMTP(
                settings.EMAIL_SMTP_HOST,
                settings.EMAIL_SMTP_PORT,
                timeout=15,
            ) as smtp:
                smtp.starttls()
                smtp.login(settings.EMAIL_SENDER, settings.EMAIL_PASSWORD)
                smtp.sendmail(
                    settings.EMAIL_SENDER,
                    [settings.EMAIL_RECEIVER],
                    message.as_string(),
                )
            self._cooldown.record_sent(fingerprint)
            self.last_error = ""
            logger.info("Email notification sent successfully.")
            return True
        except Exception as exc:
            logger.error("Failed to send email notification: %s", exc)
            self.last_error = type(exc).__name__
            return False
