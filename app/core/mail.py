"""Outgoing mail. console (local): logged and written as .eml files; smtp: sent. SES arrives in Phase 7."""

import logging
import smtplib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import make_msgid
from html import escape

from app.core.config import get_settings

log = logging.getLogger("demand_tracker.mail")


@dataclass
class Mail:
    to: list[str]
    subject: str
    text: str
    html: str | None = None
    cc: list[str] = field(default_factory=list)


# Every message sent in this process, newest last. Tests read it; nothing else should.
sent: list[Mail] = []


def _build(mail: Mail) -> EmailMessage:
    s = get_settings()
    msg = EmailMessage()
    msg["From"] = s.mail_from
    text, html = mail.text, mail.html
    if s.mail_redirect_to:
        # One inbox receives everything for now; say who each mail was really for.
        meant = "To: " + ", ".join(mail.to) + (f" | Cc: {', '.join(mail.cc)}" if mail.cc else "")
        msg["To"] = s.mail_redirect_to
        text = f"[Meant for {meant}]\n\n{text}"
        if html:
            html = f'<p style="color:#666;font-size:12px">[Meant for {escape(meant)}]</p>{html}'
    else:
        msg["To"] = ", ".join(mail.to)
        if mail.cc:
            msg["Cc"] = ", ".join(mail.cc)
    msg["Subject"] = mail.subject
    msg["Message-ID"] = make_msgid(domain="demand-tracker")
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def send(mail: Mail) -> None:
    if not mail.to:
        log.warning("Mail %r has no recipients; not sent", mail.subject)
        return
    s = get_settings()
    msg = _build(mail)
    if s.mail_backend == "smtp":
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
            if s.smtp_starttls:
                smtp.starttls()
            if s.smtp_user:
                smtp.login(s.smtp_user, s.smtp_password or "")
            smtp.send_message(msg)
    elif s.mail_backend == "console":
        folder = s.local_path(s.mail_dir)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        (folder / f"{stamp}.eml").write_bytes(bytes(msg))
        log.info("MAIL to=%s subject=%s", msg["To"], mail.subject)
    else:
        raise NotImplementedError(f"Mail backend {s.mail_backend!r} arrives in Phase 7")
    sent.append(mail)
