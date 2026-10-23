"""Tests for emailing the report. No test sends real mail: smtplib is replaced by a fake."""

import smtplib
import ssl
from datetime import datetime, timezone

import pytest

from ticket_triage.emailer import (
    EmailError,
    EmailSettings,
    build_message,
    load_email_settings,
    parse_recipients,
    resolve_recipients,
    send_report_email,
    ssl_context,
)
from ticket_triage.report import build_report_data

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)

FULL_ENV = {
    "SMTP_HOST": "smtp.test.example",
    "SMTP_PORT": "587",
    "SMTP_USERNAME": "sender@test.example",
    "SMTP_PASSWORD": "app-password-123",
    "SMTP_FROM": "reports@test.example",
    "REPORT_EMAIL_TO": "a@test.example, b@test.example",
}


def settings(**overrides):
    values = {"host": "smtp.test.example", "sender": "sender@test.example"}
    values.update(overrides)
    return EmailSettings(**values)


@pytest.fixture
def report(conn, add_ticket, tmp_path):
    """(ReportData, path of a stand-in PDF file) for one HIGH ticket."""
    add_ticket("t-1", "2026-10-17T09:00:00Z", "HIGH", subject="Site is down")
    path = tmp_path / "daily_summary.pdf"
    path.write_bytes(b"%PDF-1.4 fake report bytes")
    return build_report_data(conn, NOW), path


class TestLoadEmailSettings:
    def test_reads_every_setting(self):
        loaded = load_email_settings(FULL_ENV)

        assert loaded == EmailSettings(
            host="smtp.test.example", sender="reports@test.example", port=587,
            username="sender@test.example", password="app-password-123",
            default_recipients=("a@test.example", "b@test.example"),
        )

    def test_port_defaults_to_587_and_recipients_to_none(self):
        loaded = load_email_settings({"SMTP_HOST": "localhost", "SMTP_FROM": "me@test.example"})

        assert loaded.port == 587
        assert loaded.default_recipients == ()
        assert loaded.username == "" and loaded.password == ""

    def test_sender_defaults_to_the_username_when_it_is_an_address(self):
        env = dict(FULL_ENV)
        del env["SMTP_FROM"]

        assert load_email_settings(env).sender == "sender@test.example"

    def test_missing_host_says_how_to_fix_it(self):
        with pytest.raises(EmailError, match="SMTP_HOST is not set.*\\.env"):
            load_email_settings({})

    def test_missing_sender_is_an_error(self):
        with pytest.raises(EmailError, match="SMTP_FROM"):
            load_email_settings({"SMTP_HOST": "localhost"})

    @pytest.mark.parametrize("port", ["abc", "0", "70000", "-5"])
    def test_bad_port_is_an_error(self, port):
        with pytest.raises(EmailError, match="SMTP_PORT"):
            load_email_settings({**FULL_ENV, "SMTP_PORT": port})

    def test_username_without_password_is_an_error(self):
        env = {**FULL_ENV}
        del env["SMTP_PASSWORD"]

        with pytest.raises(EmailError, match="SMTP_PASSWORD"):
            load_email_settings(env)

    def test_invalid_default_recipient_is_an_error(self):
        with pytest.raises(EmailError, match="not a valid email address"):
            load_email_settings({**FULL_ENV, "REPORT_EMAIL_TO": "not-an-address"})

    def test_password_is_not_in_the_printed_form(self):
        assert "app-password-123" not in repr(load_email_settings(FULL_ENV))

    def test_reads_the_real_environment_by_default(self, mail_settings):
        assert load_email_settings().host == "smtp.test.example"


class TestRecipients:
    def test_splits_on_commas_and_semicolons(self):
        assert parse_recipients("a@x.example, b@y.example;c@z.example") == [
            "a@x.example", "b@y.example", "c@z.example",
        ]

    @pytest.mark.parametrize(
        "bad",
        ["plain", "a@b", "a b@c.example", "a@c.example\nBcc: evil@c.example", "<a@c.example>"],
    )
    def test_invalid_addresses_are_rejected(self, bad):
        with pytest.raises(EmailError, match="not a valid email address"):
            parse_recipients(bad)

    def test_command_line_addresses_override_the_default(self):
        defaults = settings(default_recipients=("default@test.example",))

        assert resolve_recipients(["x@test.example"], defaults) == ["x@test.example"]

    def test_default_recipients_are_used_when_none_are_given(self):
        defaults = settings(default_recipients=("default@test.example",))

        assert resolve_recipients(None, defaults) == ["default@test.example"]

    def test_no_recipient_at_all_is_an_error(self):
        with pytest.raises(EmailError, match="no recipient.*REPORT_EMAIL_TO"):
            resolve_recipients(None, settings())


class TestBuildMessage:
    def test_headers_body_and_pdf_attachment(self, report):
        data, path = report

        message = build_message(data, path, settings(), ["a@test.example", "b@test.example"])

        assert message["Subject"] == "Daily Support Ticket Summary - 2026-10-19"
        assert message["From"] == "sender@test.example"
        assert message["To"] == "a@test.example, b@test.example"
        body = message.get_body().get_content()
        assert "Needs attention first" in body and "Total tickets: 1" in body
        attachment = next(message.iter_attachments())
        assert attachment.get_content_type() == "application/pdf"
        assert attachment.get_filename() == "daily_summary.pdf"
        assert attachment.get_content() == b"%PDF-1.4 fake report bytes"

    def test_text_report_is_attached_as_plain_text(self, report, tmp_path):
        data, _ = report
        path = tmp_path / "daily_summary.txt"
        path.write_text("report text", encoding="utf-8")

        attachment = next(build_message(data, path, settings(), ["a@test.example"]).iter_attachments())

        assert attachment.get_content_type() == "text/plain"
        assert attachment.get_filename() == "daily_summary.txt"


class TestSendReportEmail:
    def test_port_587_upgrades_to_tls_then_logs_in_and_sends(self, report, fake_smtp):
        data, path = report

        send_report_email(
            data, path,
            settings(port=587, username="u@test.example", password="pw"),
            ["a@test.example"],
        )

        (connection,) = fake_smtp.connections
        assert not connection.ssl and connection.timeout == 15
        assert connection.calls == ["ehlo", "starttls", "ehlo", "login", "send_message"]
        assert connection.login_args == ("u@test.example", "pw")
        assert connection.sent[0]["To"] == "a@test.example"

    def test_port_465_uses_ssl_from_the_start(self, report, fake_smtp):
        data, path = report

        send_report_email(
            data, path,
            settings(port=465, username="u@test.example", password="pw"),
            ["a@test.example"],
        )

        (connection,) = fake_smtp.connections
        assert connection.ssl and isinstance(connection.context, ssl.SSLContext)
        assert "starttls" not in connection.calls
        assert connection.calls[-2:] == ["login", "send_message"]

    def test_local_test_server_without_tls_or_login_still_works(self, report, fake_smtp):
        data, path = report
        fake_smtp.starttls_offered = False

        send_report_email(data, path, settings(port=1025), ["a@test.example"])

        (connection,) = fake_smtp.connections
        assert connection.calls == ["ehlo", "send_message"]

    def test_password_is_never_sent_without_tls(self, report, fake_smtp):
        data, path = report
        fake_smtp.starttls_offered = False

        with pytest.raises(EmailError, match="does not offer TLS"):
            send_report_email(
                data, path, settings(username="u@test.example", password="pw"),
                ["a@test.example"],
            )

        connection = fake_smtp.connections[0]
        assert "login" not in connection.calls and not connection.sent

    def test_rejected_login_explains_app_passwords_without_the_password(
        self, report, fake_smtp
    ):
        data, path = report
        fake_smtp.login_error = smtplib.SMTPAuthenticationError(535, b"bad credentials")

        with pytest.raises(EmailError) as exc:
            send_report_email(
                data, path, settings(username="u@test.example", password="secret-pw"),
                ["a@test.example"],
            )

        assert "app password" in str(exc.value)
        assert "secret-pw" not in str(exc.value)

    @pytest.mark.parametrize(
        "error",
        [ConnectionRefusedError("refused"), TimeoutError("timed out"),
         smtplib.SMTPRecipientsRefused({"a@test.example": (550, b"no")}),
         smtplib.SMTPServerDisconnected("gone")],
    )
    def test_connection_and_server_errors_become_email_errors(self, report, fake_smtp, error):
        data, path = report
        fake_smtp.connect_error = error

        with pytest.raises(EmailError, match="could not send via smtp.test.example:587"):
            send_report_email(data, path, settings(), ["a@test.example"])


class TestSslContext:
    def test_uses_the_os_certificates_when_truststore_is_installed(self, monkeypatch):
        import sys
        import types

        class FakeTruststoreContext:
            def __init__(self, protocol):
                self.protocol = protocol

        monkeypatch.setitem(
            sys.modules, "truststore", types.SimpleNamespace(SSLContext=FakeTruststoreContext)
        )

        context = ssl_context()

        assert isinstance(context, FakeTruststoreContext)
        assert context.protocol == ssl.PROTOCOL_TLS_CLIENT

    def test_falls_back_to_pythons_default_certificates(self, monkeypatch):
        import sys

        monkeypatch.setitem(sys.modules, "truststore", None)  # as if not installed

        context = ssl_context()

        assert isinstance(context, ssl.SSLContext)
        assert context.verify_mode == ssl.CERT_REQUIRED

    def test_certificate_failure_explains_the_fix(self, report, fake_smtp):
        data, path = report
        fake_smtp.connect_error = ssl.SSLCertVerificationError(
            1, "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate"
        )

        with pytest.raises(EmailError) as exc:
            send_report_email(data, path, settings(), ["a@test.example"])

        message = str(exc.value)
        assert "truststore" in message and "Install Certificates.command" in message
