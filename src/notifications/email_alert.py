"""
Email notification integration.

Uses SMTP (Gmail by default) to send trade alerts and performance reports.
Configure in .env — leave EMAIL_SENDER blank to disable.
"""

import smtplib
from email.mime.text import MIMEText

from src.config.settings import settings


class EmailNotifier:
    """Sends email notifications via SMTP."""

    def __init__(self) -> None:
        self.enabled = bool(settings.EMAIL_SENDER and settings.EMAIL_PASSWORD)

    def send(self, subject: str, body: str) -> None:
        """Send a plain-text email.

        Does nothing if email credentials are not configured.
        """
        if not self.enabled:
            return
        raise NotImplementedError
