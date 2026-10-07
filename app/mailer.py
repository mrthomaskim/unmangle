import smtplib
from email.message import EmailMessage

from flask import current_app

from .gmail import log


def send(to, subject, text, html=None):
    cfg = current_app.config
    if not cfg.get("SMTP_HOST"):
        log("INFO", "email (SMTP not configured, not sent)", to=to, subject=subject)
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = cfg["MAIL_FROM"], to, subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=20) as s:
        s.starttls()
        if cfg.get("SMTP_USER"):
            s.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
        s.send_message(msg)
    return True
