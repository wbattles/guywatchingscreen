import smtplib
import os
import sqlite3
from pathlib import Path
from email.message import EmailMessage
from urllib.parse import urlparse

import requests
from flask import jsonify, request

from common import app, get_db, get_json_object, iso, looks_like_email, now_utc


def validate_email_recipient_json(data):
    email = (data.get("email") or "").strip()
    if not email:
        raise ValueError("Email is required.")
    if not looks_like_email(email):
        raise ValueError("Email must be a valid email address.")
    return {"email": email}


def get_smtp_password():
    secret_path = Path("/run/secrets/smtp_password")
    if secret_path.is_file():
        try:
            return secret_path.read_text().rstrip("\r\n")
        except Exception:
            app.logger.exception("Failed to read SMTP password from secret file: %s", secret_path)
            return ""
    return os.getenv("SMTP_PASSWORD", "")


def get_env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def get_env_email_settings():
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
    settings = get_env_email_settings()
    if alert_rule_id is None:
        recipients = []
    else:
        recipients = fetch_email_recipients_for_alert_rule(db, alert_rule_id)
    recipient_emails = [row["email"] for row in recipients]
    if not settings["smtp_host"] or not recipient_emails or not settings["sender_email"]:
        return "dashboard"

    email = EmailMessage()
    email["Subject"] = f"Monitor alert: {check['name']}"
    email["From"] = settings["sender_email"]
    email["To"] = ", ".join(recipient_emails)
    email.set_content(message)

    smtp_password = get_smtp_password()
    if settings["smtp_port"] == 465:
        context_cls = smtplib.SMTP_SSL
        use_starttls = False
    else:
        context_cls = smtplib.SMTP
        use_starttls = bool(settings["use_tls"])

    with context_cls(settings["smtp_host"], settings["smtp_port"], timeout=20) as server:
        if use_starttls:
            server.starttls()
        if settings["smtp_user"] and smtp_password:
            server.login(settings["smtp_user"], smtp_password)
        server.send_message(email)

    return "email"


# --- Webhook ---
# One webhook URL, from the environment, receives every alert an alert rule raises.
# The URL usually embeds a token (Discord, Slack), so it never goes in the database
# or out through the API; the API only reports which kind is configured.

def get_webhook_url():
    secret_path = Path("/run/secrets/webhook_url")
    if secret_path.is_file():
        try:
            return secret_path.read_text().strip()
        except Exception:
            app.logger.exception("Failed to read webhook URL from secret file: %s", secret_path)
            return ""
    return os.getenv("WEBHOOK_URL", "").strip()


def webhook_kind(url):
    host = (urlparse(url).hostname or "").lower()
    if host in ("discord.com", "discordapp.com") or host.endswith((".discord.com", ".discordapp.com")):
        return "discord"
    if host == "hooks.slack.com":
        return "slack"
    return "generic"


def get_webhook_settings():
    url = get_webhook_url()
    return {"configured": bool(url), "kind": webhook_kind(url) if url else None}


ALERT_COLOR = 0xE5484D  # red; matches the dashboard's "down" colour


def alert_title(check):
    return f"\U0001F534 {check['name']} is down"


def rule_summary(context):
    if not context:
        return None
    plural = "failure" if context["failures"] == 1 else "failures"
    return (
        f"{context['failures']} {plural} in the last {context['window_minutes']} min "
        f"(rule \"{context['rule_name']}\" fires at {context['threshold']})"
    )


def webhook_text(check, message, detail=None, context=None):
    lines = [alert_title(check)]
    lines.append(detail or message)
    summary = rule_summary(context)
    if summary:
        lines.append(summary)
    lines.append(check["url"])
    return "\n".join(lines)


def discord_payload(check, message, detail=None, context=None):
    embed = {
        "title": alert_title(check)[:256],
        "description": (detail or message)[:4096],
        "color": ALERT_COLOR,
        "fields": [{"name": "URL", "value": check["url"][:1024], "inline": False}],
        "footer": {"text": "guywatchingscreen"},
        "timestamp": now_utc().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    if check["url"].startswith(("http://", "https://")):
        embed["url"] = check["url"]
    if context:
        plural = "failure" if context["failures"] == 1 else "failures"
        embed["fields"] += [
            {"name": "Alert rule", "value": str(context["rule_name"])[:1024], "inline": True},
            {"name": "Failures", "value": f"{context['failures']} {plural} in {context['window_minutes']} min", "inline": True},
            {"name": "Fires at", "value": f"{context['threshold']} in {context['window_minutes']} min", "inline": True},
        ]
    # Never let alert text ping anyone.
    return {"username": "guywatchingscreen", "allowed_mentions": {"parse": []}, "embeds": [embed]}


def send_webhook_alert(check, message, detail=None, context=None):
    """POST the alert to WEBHOOK_URL. Returns True when sent, False when no webhook is set."""
    url = get_webhook_url()
    if not url:
        return False
    if not url.startswith(("http://", "https://")):
        raise ValueError("WEBHOOK_URL must start with http:// or https://")

    kind = webhook_kind(url)
    if kind == "discord":
        response = requests.post(url, json=discord_payload(check, message, detail, context), timeout=10)
    elif kind == "slack":
        response = requests.post(url, json={"text": webhook_text(check, message, detail, context)}, timeout=10)
    else:
        # Plain text works for ntfy and most generic receivers; ntfy reads the Title header.
        response = requests.post(
            url,
            data=webhook_text(check, message, detail, context).encode("utf-8"),
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "Title": f"{check['name']} is down".encode("ascii", "replace").decode(),
                "Tags": "red_circle",
            },
            timeout=10,
        )
    response.raise_for_status()
    return True


# --- API routes ---

@app.route("/api/communication")
def api_communication():
    email_settings = get_env_email_settings()
    email_recipients = fetch_email_recipients(get_db())
    return jsonify({
        "email_settings": email_settings,
        "email_recipients": [dict(r) for r in email_recipients],
        "webhook": get_webhook_settings(),
    })


@app.route("/api/communication/emails", methods=["POST"])
def api_create_email_recipient():
    try:
        data = validate_email_recipient_json(get_json_object(request))
        db = get_db()
        db.execute(
            "INSERT INTO communication_email_recipients (email, created_at) VALUES (?, ?)",
            (data["email"], iso(now_utc())),
        )
        db.commit()
        return jsonify({"ok": True}), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except sqlite3.IntegrityError:
        return jsonify({"error": "That email already exists."}), 409


@app.route("/api/communication/emails/<int:recipient_id>", methods=["PUT"])
def api_update_email_recipient(recipient_id):
    db = get_db()
    recipient = db.execute(
        "SELECT * FROM communication_email_recipients WHERE id = ?",
        (recipient_id,),
    ).fetchone()
    if recipient is None:
        return jsonify({"error": "Email not found."}), 404
    try:
        data = validate_email_recipient_json(get_json_object(request))
        db.execute(
            "UPDATE communication_email_recipients SET email = ? WHERE id = ?",
            (data["email"], recipient_id),
        )
        db.commit()
        return jsonify({"ok": True})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except sqlite3.IntegrityError:
        return jsonify({"error": "That email already exists."}), 409


@app.route("/api/communication/emails/<int:recipient_id>", methods=["DELETE"])
def api_delete_email_recipient(recipient_id):
    db = get_db()
    recipient = db.execute(
        "SELECT id FROM communication_email_recipients WHERE id = ?",
        (recipient_id,),
    ).fetchone()
    if recipient is None:
        return jsonify({"error": "Email not found."}), 404
    db.execute("DELETE FROM alert_rule_email_recipients WHERE recipient_id = ?", (recipient_id,))
    db.execute("DELETE FROM communication_email_recipients WHERE id = ?", (recipient_id,))
    db.commit()
    return jsonify({"ok": True})
