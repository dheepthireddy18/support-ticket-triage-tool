"""
Email the daily report (optional). Uses only the standard library.

Settings come from environment variables, or from a `.env` file in the current
folder when python-dotenv is installed (the same convention as the LLM rules;
values already set in the environment win over the file):

    SMTP_HOST         required  mail server, e.g. smtp.gmail.com
    SMTP_PORT         optional  default 587 (STARTTLS); 465 uses SSL from the start
    SMTP_USERNAME     optional  login name; leave out for a local test server
    SMTP_PASSWORD     required when SMTP_USERNAME is set (an app password, not your own)
    SMTP_FROM         optional  sender address; defaults to SMTP_USERNAME if that is an address
    REPORT_EMAIL_TO   optional  default recipient(s), comma separated

The password is never printed or logged. If the server offers TLS the connection
is upgraded before logging in, and the tool refuses to send a password over a
connection that cannot be encrypted. The server's certificate is checked against
the operating system's certificates when `truststore` is installed (the `email`
extra), as the Jev rule does; Python from python.org on a Mac otherwise has no
certificates until its "Install Certificates.command" script is run.
"""

from __future__ import annotations

import mimetypes
import os
import re
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Mapping

from ticket_triage.priority.llm_rule import load_dotenv_file
from ticket_triage.report import REPORT_TITLE, ReportData, render_report

HOST_VAR = "SMTP_HOST"
PORT_VAR = "SMTP_PORT"
USERNAME_VAR = "SMTP_USERNAME"
PASSWORD_VAR = "SMTP_PASSWORD"
SENDER_VAR = "SMTP_FROM"
RECIPIENTS_VAR = "REPORT_EMAIL_TO"

DEFAULT_PORT = 587
SSL_PORT = 465
TIMEOUT_SECONDS = 15

# Deliberately simple: no spaces, quotes, angle brackets or line breaks, so an
# address can never add headers to the message.
ADDRESS_PATTERN = re.compile(r"^[^@\s,;<>\"']+@[^@\s,;<>\"']+\.[^@\s,;<>\"']+$")


class EmailError(Exception):
    """The email could not be set up or sent. The message says what to fix."""


@dataclass(frozen=True)
class EmailSettings:
    """Mail server settings and the default recipients."""

    host: str
    sender: str
    port: int = DEFAULT_PORT
    username: str = ""
    password: str = field(default="", repr=False)  # Kept out of any printed form
    default_recipients: tuple[str, ...] = ()


# --- Settings -----------------------------------------------------------------


def parse_recipients(text: str) -> list[str]:
    """Split a comma or semicolon separated list and check every address."""
    addresses = [part.strip() for part in re.split(r"[,;]", text) if part.strip()]
    for address in addresses:
        if not ADDRESS_PATTERN.match(address):
            raise EmailError(f"'{address}' is not a valid email address")
    return addresses


def load_email_settings(environ: Mapping[str, str] | None = None) -> EmailSettings:
    """
    Read the mail settings. With no argument, reads `.env` (if python-dotenv is
    installed) and then the process environment.

    :raises EmailError: if a required setting is missing or invalid.
    """
    if environ is None:
        load_dotenv_file()
        environ = os.environ

    def get(name: str) -> str:
        return environ.get(name, "").strip()

    host = get(HOST_VAR)
    if not host:
        raise EmailError(
            f"{HOST_VAR} is not set. Add the mail server settings to .env "
            "(see .env.example) to send the report by email."
        )

    port_text = get(PORT_VAR)
    try:
        port = int(port_text) if port_text else DEFAULT_PORT
    except ValueError:
        raise EmailError(f"{PORT_VAR} must be a number, got '{port_text}'") from None
    if not 1 <= port <= 65535:
        raise EmailError(f"{PORT_VAR} must be between 1 and 65535, got {port}")

    username, password = get(USERNAME_VAR), environ.get(PASSWORD_VAR, "")
    if username and not password:
        raise EmailError(f"{PASSWORD_VAR} is not set but {USERNAME_VAR} is")

    sender = get(SENDER_VAR) or (username if "@" in username else "")
    if not sender:
        raise EmailError(f"{SENDER_VAR} is not set (the address the report is sent from)")
    if not ADDRESS_PATTERN.match(sender):
        raise EmailError(f"{SENDER_VAR} '{sender}' is not a valid email address")

    return EmailSettings(
        host=host,
        sender=sender,
        port=port,
        username=username,
        password=password,
        default_recipients=tuple(parse_recipients(get(RECIPIENTS_VAR))),
    )


def resolve_recipients(given: list[str] | None, settings: EmailSettings) -> list[str]:
    """The addresses passed on the command line, else the default recipients."""
    if given:
        recipients = [a for text in given for a in parse_recipients(text)]
    else:
        recipients = list(settings.default_recipients)
    if not recipients:
        raise EmailError(
            f"no recipient: pass --email-to ADDRESS or set {RECIPIENTS_VAR} in .env"
        )
    return recipients


# --- Message and sending ------------------------------------------------------


def ssl_context() -> ssl.SSLContext:
    """
    A TLS context that verifies the mail server against the OS certificate store
    when truststore is installed, else Python's own default certificates.
    """
    try:
        import truststore
    except ImportError:
        return ssl.create_default_context()
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def build_message(
    data: ReportData, report_path: Path, settings: EmailSettings, recipients: list[str]
) -> EmailMessage:
    """The email: the text report as the body and the report file attached."""
    message = EmailMessage()
    message["Subject"] = f"{REPORT_TITLE} - {data.generated_at:%Y-%m-%d}"
    message["From"] = settings.sender
    message["To"] = ", ".join(recipients)
    message.set_content(
        f"The daily support ticket summary for {data.generated_at:%Y-%m-%d} is below "
        f"and attached as {report_path.name}.\n\n{render_report(data)}"
    )

    kind, _ = mimetypes.guess_type(report_path.name)
    maintype, _, subtype = (kind or "application/octet-stream").partition("/")
    message.add_attachment(
        report_path.read_bytes(), maintype=maintype, subtype=subtype, filename=report_path.name
    )
    return message


def send_report_email(
    data: ReportData, report_path: Path, settings: EmailSettings, recipients: list[str]
) -> None:
    """
    Send the report. Upgrades to TLS before logging in whenever the server allows it.

    :raises EmailError: with a message saying what went wrong (the password is never in it).
    """
    message = build_message(data, report_path, settings, recipients)
    context = ssl_context()
    use_ssl = settings.port == SSL_PORT

    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(
                settings.host, settings.port, timeout=TIMEOUT_SECONDS, context=context
            )
        else:
            server = smtplib.SMTP(settings.host, settings.port, timeout=TIMEOUT_SECONDS)
        with server:
            server.ehlo()
            if not use_ssl:
                if server.has_extn("starttls"):
                    server.starttls(context=context)
                    server.ehlo()
                elif settings.username:
                    raise EmailError(
                        f"{settings.host}:{settings.port} does not offer TLS, so the password "
                        "was not sent. Use a port with TLS (587 or 465)."
                    )
            if settings.username:
                server.login(settings.username, settings.password)
            server.send_message(message)
    except EmailError:
        raise
    except smtplib.SMTPAuthenticationError:
        raise EmailError(
            f"the server rejected the login. Check {USERNAME_VAR} and {PASSWORD_VAR} "
            "(Gmail and Outlook need an app password, not your normal password)."
        ) from None
    except ssl.SSLCertVerificationError as exc:
        detail = getattr(exc, "verify_message", None) or str(exc)
        raise EmailError(
            f"could not verify the certificate of {settings.host} ({detail}). Install the "
            "truststore package (pip install -e \".[email]\"), or, with Python from python.org "
            "on a Mac, run \"Install Certificates.command\" from the Python folder in "
            "Applications. A company network that inspects HTTPS also needs truststore."
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailError(f"could not send via {settings.host}:{settings.port}: {exc}") from exc
