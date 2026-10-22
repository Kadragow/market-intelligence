"""E-mail delivery over SMTP.

A message is sent only when all three hold: SMTP configuration is complete,
``--dry-run`` is off, and sending is explicitly enabled (MI_EMAIL_ENABLED=true).
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from market_intelligence.config import Settings

logger = logging.getLogger(__name__)


class EmailError(Exception):
    """Sending failed. Message is safe to log (no credentials)."""


@dataclass(frozen=True)
class OutgoingEmail:
    subject: str
    text: str
    html: str
    sender: str
    recipients: list[str]


class EmailSender(Protocol):
    def send(self, message: OutgoingEmail) -> None: ...


@dataclass(frozen=True)
class SendDecision:
    send: bool
    reason: str


def decide_sending(settings: Settings, *, dry_run: bool) -> SendDecision:
    if dry_run:
        return SendDecision(False, "dry-run mode (use --send to deliver)")
    if not settings.email_enabled:
        return SendDecision(False, "sending not enabled (MI_EMAIL_ENABLED is not true)")
    missing = settings.smtp_missing_fields()
    if missing:
        return SendDecision(False, f"SMTP configuration incomplete: missing {', '.join(missing)}")
    return SendDecision(True, "all conditions met")


def build_message(email: OutgoingEmail) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = email.subject
    msg["From"] = email.sender
    msg["To"] = ", ".join(email.recipients)
    msg.set_content(email.text, charset="utf-8")
    msg.add_alternative(email.html, subtype="html", charset="utf-8")
    return msg


class SmtpEmailSender:
    def __init__(self, settings: Settings, timeout_seconds: float = 30.0) -> None:
        host, username, password = (
            settings.smtp_host,
            settings.smtp_username,
            settings.smtp_password,
        )
        missing = settings.smtp_missing_fields()
        if missing or not host or not username or password is None:
            raise EmailError(f"SMTP configuration incomplete: missing {', '.join(missing)}")
        self._host = host
        self._port = settings.smtp_port
        self._username = username
        self._password = password
        self._starttls = settings.smtp_use_starttls
        self._timeout = timeout_seconds

    def send(self, message: OutgoingEmail) -> None:
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
                if self._starttls:
                    smtp.starttls(context=ssl.create_default_context())
                smtp.login(self._username, self._password.get_secret_value())
                smtp.send_message(build_message(message))
        except smtplib.SMTPAuthenticationError as exc:
            raise EmailError(f"SMTP authentication failed (code {exc.smtp_code})") from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise EmailError(f"SMTP error: {type(exc).__name__}") from exc
        logger.info("E-mail sent to %d recipient(s)", len(message.recipients))


class RecordingEmailSender:
    """Test double: records messages instead of sending them."""

    def __init__(self, error: EmailError | None = None) -> None:
        self.sent: list[OutgoingEmail] = []
        self._error = error

    def send(self, message: OutgoingEmail) -> None:
        if self._error:
            raise self._error
        self.sent.append(message)
