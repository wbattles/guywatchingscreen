import smtplib
from email.message import EmailMessage

from flask import flash, redirect, render_template, request, url_for

from common import app, get_db, looks_like_email, parse_int_field


def validate_email_settings_form(form):
    recipient_email = form.get("recipient_email", "").strip()
    sender_email = form.get("sender_email", "").strip()
    smtp_host = form.get("smtp_host", "").strip()
    smtp_port = parse_int_field(form, "smtp_port", 587, "SMTP port")
    smtp_user = form.get("smtp_user", "").strip()
    smtp_password = form.get("smtp_password", "")
    use_tls = 1 if form.get("use_tls") == "on" else 0

    has_any_email_setting = any([recipient_email, sender_email, smtp_host, smtp_user, smtp_password])

    if recipient_email and not looks_like_email(recipient_email):
        raise ValueError("Recipient email must be a valid email address.")
    if sender_email and not looks_like_email(sender_email):
        raise ValueError("Sender email must be a valid email address.")
    if smtp_port < 1 or smtp_port > 65535:
        raise ValueError("SMTP port must be between 1 and 65535.")
    if has_any_email_setting:
        if not recipient_email:
            raise ValueError("Recipient email is required for email alerts.")
        if not sender_email:
            raise ValueError("Sender email is required for email alerts.")
        if not smtp_host:
            raise ValueError("SMTP host is required for email alerts.")

    return {
        "recipient_email": recipient_email,
        "sender_email": sender_email,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_user": smtp_user,
        "smtp_password": smtp_password,
        "use_tls": use_tls,
    }


def fetch_email_settings(db):
    settings = db.execute("SELECT * FROM communication_email_settings WHERE id = 1").fetchone()
    if settings is None:
        return {
            "recipient_email": "",
            "sender_email": "",
            "smtp_host": "",
            "smtp_port": 587,
            "smtp_user": "",
            "smtp_password": "",
            "use_tls": 1,
        }
    return settings


def send_email_alert(db, check, message):
    settings = fetch_email_settings(db)
    if not settings["smtp_host"] or not settings["recipient_email"] or not settings["sender_email"]:
        return "dashboard"

    email = EmailMessage()
    email["Subject"] = f"Monitor alert: {check['name']}"
    email["From"] = settings["sender_email"]
    email["To"] = settings["recipient_email"]
    email.set_content(message)

    with smtplib.SMTP(settings["smtp_host"], settings["smtp_port"], timeout=20) as server:
        if settings["use_tls"]:
            server.starttls()
        if settings["smtp_user"] and settings["smtp_password"]:
            server.login(settings["smtp_user"], settings["smtp_password"])
        server.send_message(email)

    return "email"


def communication_data():
    db = get_db()
    return fetch_email_settings(db)


@app.route("/communication", methods=["GET", "POST"])
def communication_screen():
    db = get_db()
    if request.method == "POST":
        try:
            data = validate_email_settings_form(request.form)
            db.execute(
                """
                INSERT INTO communication_email_settings (
                    id, recipient_email, sender_email, smtp_host, smtp_port,
                    smtp_user, smtp_password, use_tls
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    recipient_email = excluded.recipient_email,
                    sender_email = excluded.sender_email,
                    smtp_host = excluded.smtp_host,
                    smtp_port = excluded.smtp_port,
                    smtp_user = excluded.smtp_user,
                    smtp_password = excluded.smtp_password,
                    use_tls = excluded.use_tls
                """,
                (
                    data["recipient_email"],
                    data["sender_email"],
                    data["smtp_host"],
                    data["smtp_port"],
                    data["smtp_user"],
                    data["smtp_password"],
                    data["use_tls"],
                ),
            )
            db.commit()
            flash("Communication settings saved.", "success")
            return redirect(url_for("communication_screen"))
        except ValueError as exc:
            flash(str(exc), "error")

    email_settings = communication_data()
    return render_template("communication.html", email_settings=email_settings)
