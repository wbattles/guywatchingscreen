import os
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import jsonify

from common import app, now_utc


# --- Webhook ---
# Alerts go out through one webhook URL, from the environment: every alert an alert rule raises.
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
    return jsonify({"webhook": get_webhook_settings()})
