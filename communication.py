import smtplib
import os
from pathlib import Path
from email.message import EmailMessage

from flask import flash, redirect, render_template, request, url_for

from common import app, get_db, looks_like_email


def validate_email_recipient_form(form):
    email = form.get("email", "").strip()
    if not email:
        raise ValueError("Email is required.")
    if not looks_like_email(email):
        raise ValueError("Email must be a valid email address.")
    return {"email": email}


def get_smtp_password():
    """Return the SMTP password.

    The password is read from a Docker secret file at ``/run/secrets/smtp_password``
    if it exists, otherwise from the ``SMTP_PASSWORD`` environment variable.
    Returns an empty string if neither is set.
    """
    secret_path = Path("/run/secrets/smtp_password")
    if secret_path.is_file():
        try:
            return secret_path.read_text().strip()
        except Exception:
            return ""
    return os.getenv("SMTP_PASSWORD", "")


def get_env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def get_env_email_settings():
    """Read email configuration from environment variables or Docker secrets.

    Expected environment variables:
        SENDER_EMAIL   – From address
        SMTP_HOST      – Hostname of SMTP server
        SMTP_PORT      – Port (defaults to 587)
        SMTP_USER      – Username (optional)
        SMTP_USE_TLS   – "1" or "0" (defaults to 1)
        SMTP_PASSWORD  – Password (read from env or secret file via get_smtp_password())
    """
    return {
        "sender_email": os.getenv("SENDER_EMAIL", ""),
        "smtp_host": os.getenv("SMTP_HOST", ""),
        "smtp_port": get_env_int("SMTP_PORT", 587),
        "smtp_user": os.getenv("SMTP_USER", ""),
        "use_tls": get_env_int("SMTP_USE_TLS", 1),
    }

def fetch_email_recipients(db):
    return db.execute(
        "SELECT * FROM communication_email_recipients ORDER BY email COLLATE NOCASE ASC"
    ).fetchall()


def fetch_email_recipients_for_alert_rule(db, alert_rule_id):
    return db.execute(
        """
        SELECT r.*
        FROM communication_email_recipients r
        JOIN alert_rule_email_recipients arer ON arer.recipient_id = r.id
        WHERE arer.alert_rule_id = ?
        ORDER BY r.email COLLATE NOCASE ASC
        """,
        (alert_rule_id,),
    ).fetchall()


def send_email_alert(db, check, message, alert_rule_id=None):
    # Email configuration is now sourced from environment variables / Docker secrets.
    settings = get_env_email_settings()
    if alert_rule_id is None:
        recipients = []
    else:
        recipients = fetch_email_recipients_for_alert_rule(db, alert_rule_id)
    recipient_emails = [row["email"] for row in recipients]
    # Require host, at least one recipient, and a sender address.
    if not settings["smtp_host"] or not recipient_emails or not settings["sender_email"]:
        return "dashboard"

    email = EmailMessage()
    email["Subject"] = f"Monitor alert: {check['name']}"
    email["From"] = settings["sender_email"]
    email["To"] = ", ".join(recipient_emails)
    email.set_content(message)

    with smtplib.SMTP(settings["smtp_host"], settings["smtp_port"], timeout=20) as server:
        if settings["use_tls"]:
            server.starttls()
        smtp_password = get_smtp_password()
        if settings["smtp_user"] and smtp_password:
            server.login(settings["smtp_user"], smtp_password)
        server.send_message(email)

    return "email"


@app.route("/communication")
def communication_screen():
    email_settings = get_env_email_settings()
    email_recipients = fetch_email_recipients(get_db())
    return render_template("communication.html", email_settings=email_settings, email_recipients=email_recipients)


@app.route("/communication/emails/new", methods=["GET", "POST"])
def create_email_recipient():
    db = get_db()
    if request.method == "POST":
        try:
            data = validate_email_recipient_form(request.form)
            db.execute(
                "INSERT INTO communication_email_recipients (email, created_at) VALUES (?, datetime('now'))",
                (data["email"],),
            )
            db.commit()
            flash("Email added.", "success")
            return redirect(url_for("communication_screen"))
        except ValueError as exc:
            flash(str(exc), "error")
        except Exception:
            flash("That email already exists.", "error")
    return render_template("communication_email_form.html", recipient=None)


@app.route("/communication/emails/<int:recipient_id>/edit", methods=["GET", "POST"])
def edit_email_recipient(recipient_id):
    db = get_db()
    recipient = db.execute(
        "SELECT * FROM communication_email_recipients WHERE id = ?",
        (recipient_id,),
    ).fetchone()
    if recipient is None:
        flash("Email not found.", "error")
        return redirect(url_for("communication_screen"))

    if request.method == "POST":
        try:
            data = validate_email_recipient_form(request.form)
            db.execute(
                "UPDATE communication_email_recipients SET email = ? WHERE id = ?",
                (data["email"], recipient_id),
            )
            db.commit()
            flash("Email updated.", "success")
            return redirect(url_for("communication_screen"))
        except ValueError as exc:
            flash(str(exc), "error")
        except Exception:
            flash("That email already exists.", "error")

    return render_template("communication_email_form.html", recipient=recipient)


@app.post("/communication/emails/<int:recipient_id>/delete")
def delete_email_recipient(recipient_id):
    db = get_db()
    db.execute("DELETE FROM communication_email_recipients WHERE id = ?", (recipient_id,))
    db.commit()
    flash("Email deleted.", "success")
    return redirect(url_for("communication_screen"))
