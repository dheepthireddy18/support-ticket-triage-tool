"""Shared pytest fixtures: ticket factories, priority-rule context, and test databases."""

import json
import smtplib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ticket_triage.db import init_db
from ticket_triage.models import RejectedRecord, Ticket
from ticket_triage.priority.base import TriageContext

SAMPLE_FILE = Path(__file__).resolve().parents[1] / "data" / "sample_tickets.json"
RULES_FILE = Path(__file__).resolve().parents[1] / "data" / "priority_rules.json"

FIXED_NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


# --- Priority rule fixtures ---------------------------------------------------


@pytest.fixture
def fixed_now():
    return FIXED_NOW


@pytest.fixture
def make_ticket():
    """Factory: build a Ticket with sensible defaults, overriding only what a test needs."""
    counter = iter(range(1, 10_000))

    def _make(**overrides):
        n = next(counter)
        fields = {
            "id": f"t-{n}",
            "subject": "General question",
            "body": f"Something about the product ({n}).",
            "customer": f"CUST-{n:03d}",
            "created_at": "2026-10-19T12:00:00Z",
        }
        fields.update(overrides)
        return Ticket(**fields)

    return _make


@pytest.fixture
def ctx(fixed_now):
    return TriageContext(now=fixed_now)


@pytest.fixture
def sample_tickets():
    """The five valid tickets from data/sample_tickets.json."""
    records = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))
    seen = set()
    tickets = []
    for record in records:
        if not record.get("customer") or record["id"] in seen:
            continue
        if record["created_at"] == "yesterday afternoon":
            continue
        seen.add(record["id"])
        tickets.append(Ticket.from_dict(record))
    return tickets


# --- Database fixtures --------------------------------------------------------


@pytest.fixture
def conn():
    """An empty in-memory database with the full schema, closed after the test."""
    connection = init_db(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def add_ticket(conn):
    """
    Factory: insert one ticket into `conn`.

    add_ticket("a", "2026-10-19T08:00:00Z", "HIGH") stores a ticket with id "a".
    Subject, body and customer are derived from the id; pass keyword arguments
    (e.g. customer="ACME") to override any Ticket field.
    """

    def _add(ticket_id, created_at, priority=None, **overrides):
        fields = {
            "id": ticket_id,
            "subject": f"Subject {ticket_id}",
            "body": f"Body {ticket_id}",
            "customer": f"CUST-{ticket_id}",
            "created_at": created_at,
            "priority_level": priority,
        }
        fields.update(overrides)
        ticket = Ticket(**fields)
        conn.execute(
            "INSERT INTO tickets (id, content_hash, subject, body, customer, "
            "created_at, inserted_at, status, priority_level, classified_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ticket.to_db_tuple(),
        )
        conn.commit()

    return _add


@pytest.fixture
def add_rejected(conn):
    """Factory: insert one rejected record into `conn`."""

    def _add(reason="missing customer"):
        record = RejectedRecord(raw_record={"id": "x"}, rejection_reason=reason)
        conn.execute(
            "INSERT INTO rejected_tickets (raw_record, rejection_reason, rejected_at) "
            "VALUES (?, ?, ?)",
            record.to_db_tuple(),
        )
        conn.commit()

    return _add


# --- Email fixtures -----------------------------------------------------------

EMAIL_VARS = (
    "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM", "REPORT_EMAIL_TO",
)


@pytest.fixture
def fake_smtp(monkeypatch):
    """
    Replace smtplib.SMTP and smtplib.SMTP_SSL with a recorder, so no test sends mail.

    The returned controller has `connections` (one per SMTP object created, each with
    `calls`, `sent`, `host`, `port`, `ssl`) and switches to simulate a server:
    `starttls_offered`, `login_error` and `connect_error`.
    """

    class Controller:
        starttls_offered = True
        login_error = None
        connect_error = None

        def __init__(self):
            self.connections = []

    controller = Controller()

    class FakeSMTP:
        uses_ssl = False

        def __init__(self, host, port, timeout=None, context=None):
            if controller.connect_error is not None:
                raise controller.connect_error
            self.host, self.port, self.timeout, self.context = host, port, timeout, context
            self.ssl = self.uses_ssl
            self.calls, self.sent = [], []
            controller.connections.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def ehlo(self):
            self.calls.append("ehlo")

        def has_extn(self, name):
            return name == "starttls" and controller.starttls_offered

        def starttls(self, context=None):
            self.calls.append("starttls")

        def login(self, username, password):
            self.calls.append("login")
            if controller.login_error is not None:
                raise controller.login_error
            self.login_args = (username, password)

        def send_message(self, message):
            self.calls.append("send_message")
            self.sent.append(message)

    class FakeSMTPSSL(FakeSMTP):
        uses_ssl = True

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTPSSL)
    return controller


@pytest.fixture
def mail_settings(monkeypatch):
    """Mail settings in the environment, without ever reading the real .env file."""
    monkeypatch.setattr("ticket_triage.emailer.load_dotenv_file", lambda: None)
    for name in EMAIL_VARS:
        monkeypatch.delenv(name, raising=False)
    values = {
        "SMTP_HOST": "smtp.test.example",
        "SMTP_PORT": "587",
        "SMTP_USERNAME": "sender@test.example",
        "SMTP_PASSWORD": "app-password-123",
        "SMTP_FROM": "sender@test.example",
        "REPORT_EMAIL_TO": "manager@test.example",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values
