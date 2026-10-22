import smtplib
from pathlib import Path
from typing import Any, ClassVar

import pytest

from market_intelligence.config import Settings
from market_intelligence.delivery.email import (
    EmailError,
    OutgoingEmail,
    SmtpEmailSender,
    build_message,
    decide_sending,
)

MESSAGE = OutgoingEmail(
    subject="Tygodniowy brief — zażółć",
    text="Treść z polskimi znakami: ąęśćżźół",
    html="<p>Treść: ąęśćżźół</p>",
    sender="bot@example.com",
    recipients=["a@example.com", "b@example.com"],
)


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MI_EMAIL_ENABLED", "true")
    monkeypatch.setenv("MI_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("MI_SMTP_USERNAME", "user")
    monkeypatch.setenv("MI_SMTP_PASSWORD", "smtp-super-secret")
    monkeypatch.setenv("MI_EMAIL_FROM", "bot@example.com")
    monkeypatch.setenv("MI_EMAIL_TO", "a@example.com")
    return Settings()


class FakeSMTP:
    instances: ClassVar[list["FakeSMTP"]] = []
    fail_with: ClassVar[Exception | None] = None

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self.actions: list[str] = []
        self.sent: list[Any] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *args: object) -> None:
        self.actions.append("quit")

    def starttls(self, context: object) -> None:
        self.actions.append("starttls")

    def login(self, user: str, password: str) -> None:
        if FakeSMTP.fail_with:
            raise FakeSMTP.fail_with
        self.actions.append(f"login:{user}")

    def send_message(self, msg: Any) -> None:
        self.sent.append(msg)


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSMTP.instances = []
    FakeSMTP.fail_with = None
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)


def test_decide_sending_requires_all_conditions(settings: Settings) -> None:
    assert decide_sending(settings, dry_run=False).send
    assert not decide_sending(settings, dry_run=True).send


def test_smtp_sender_uses_starttls_and_login(settings: Settings) -> None:
    SmtpEmailSender(settings).send(MESSAGE)
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.example.com", 587)
    assert smtp.actions == ["starttls", "login:user", "quit"]
    assert len(smtp.sent) == 1


def test_smtp_auth_error_does_not_leak_password(settings: Settings) -> None:
    FakeSMTP.fail_with = smtplib.SMTPAuthenticationError(535, b"bad credentials")
    with pytest.raises(EmailError) as info:
        SmtpEmailSender(settings).send(MESSAGE)
    assert "535" in str(info.value)
    assert "smtp-super-secret" not in str(info.value)


def test_smtp_connection_error_is_wrapped(settings: Settings) -> None:
    FakeSMTP.fail_with = ConnectionRefusedError("refused")
    with pytest.raises(EmailError, match="ConnectionRefusedError"):
        SmtpEmailSender(settings).send(MESSAGE)


def test_sender_refuses_incomplete_config(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MI_SMTP_HOST")
    with pytest.raises(EmailError, match="MI_SMTP_HOST"):
        SmtpEmailSender(Settings())


def test_message_is_utf8_multipart() -> None:
    msg = build_message(MESSAGE)
    assert msg["To"] == "a@example.com, b@example.com"
    assert msg.is_multipart()
    parts = [p.get_content() for p in msg.iter_parts()]
    assert "ąęśćżźół" in parts[0]
    assert "ąęśćżźół" in parts[1]
    assert msg["Subject"] == "Tygodniowy brief — zażółć"
