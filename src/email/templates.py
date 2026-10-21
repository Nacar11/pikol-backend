from src.config.settings import get_settings
from src.email.port import EmailMessage


def _link(path: str, token: str) -> str:
    return f"{get_settings().frontend_base_url}{path}?token={token}"


def verification_email(to: str, first_name: str, token: str) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject="Verify your Pikol email",
        body=(
            f"Hi {first_name},\n\n"
            f"Confirm your email to start booking courts:\n"
            f"{_link('/verify-email', token)}\n\n"
            f"The link expires in 24 hours. If you did not sign up, ignore this email."
        ),
    )


def password_reset_email(to: str, first_name: str, token: str) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject="Reset your Pikol password",
        body=(
            f"Hi {first_name},\n\n"
            f"Set a new password here:\n"
            f"{_link('/reset-password', token)}\n\n"
            f"The link expires in 60 minutes. If you did not ask for this, ignore this email."
        ),
    )


def invitation_email(to: str, first_name: str, token: str, expiry_days: int) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject="You have been invited to Pikol",
        body=(
            f"Hi {first_name},\n\n"
            f"You have been added as staff on Pikol. Set your password to "
            f"activate your account:\n"
            f"{_link('/accept-invite', token)}\n\n"
            f"The invitation expires in {expiry_days} days."
        ),
    )
