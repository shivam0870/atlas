"""Email adapter. Development delivery is confined to the local Mailpit inbox."""

import asyncio
import smtplib
from email.message import EmailMessage

from fastapi import HTTPException

from atlas.config import settings


async def send_mail(recipient: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    def deliver() -> None:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)

    try:
        await asyncio.to_thread(deliver)
    except (OSError, smtplib.SMTPException) as exc:
        raise HTTPException(
            503, "Email delivery is unavailable. Please try again shortly."
        ) from exc
