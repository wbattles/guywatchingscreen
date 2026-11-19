import os
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import jsonify

from common import app, now_utc


# --- Webhook ---
# Alerts go out through one webhook URL, from the environment: every alert an alert rule raises.
# The URL usually embeds a token, so it never goes in the database
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


# Each format is what that service's incoming webhook expects. "json" is the catch-all:
# it carries "text" (Slack-style: Mattermost, Rocket.Chat, Zulip), "content" (Discord-style)
# and the alert as structured fields for n8n, Home Assistant, Zapier or your own receiver.
WEBHOOK_FORMATS = ("discord", "slack", "teams", "ntfy", "json")


def detect_webhook_format(url):
    host = (urlparse(url).hostname or "").lower()
    if host in ("discord.com", "discordapp.com") or host.endswith((".discord.com", ".discordapp.com")):
        return "discord"
    # Slack, Google Chat and classic Teams connectors all take {"text": ...} and nothing else.
    if host in ("hooks.slack.com", "chat.googleapis.com") or host.endswith(".webhook.office.com"):
        return "slack"
    # Teams Workflows (Power Automate) take an Adaptive Card.
    if host.endswith((".logic.azure.com", ".api.powerplatform.com")):
        return "teams"
    if host == "ntfy.sh" or host.startswith("ntfy.") or ".ntfy." in host:
        return "ntfy"
    return "json"


def webhook_kind(url):
    """WEBHOOK_FORMAT wins when set to a known format; otherwise guess from the URL."""
    forced = os.getenv("WEBHOOK_FORMAT", "").strip().lower()
    if forced in WEBHOOK_FORMATS:
        return forced
    return detect_webhook_format(url)


def get_webhook_settings():
    url = get_webhook_url()
    return {"configured": bool(url), "kind": webhook_kind(url) if url else None}


ALERT_COLOR = 0xE5484D  # red; matches the dashboard's "down" colour


def alert_title(check):
    return f"\U0001F534 {check['name']} is down"


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def rule_summary(context):
    if not context:
        return None
    return (
        f"{_plural(context['failures'], 'failure')} in the last {context['window_minutes']} min "
        f"(rule \"{context['rule_name']}\" fires at {context['threshold']})"
    )


def _timestamp():
    return now_utc().replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
        "timestamp": _timestamp(),
    }
    if check["url"].startswith(("http://", "https://")):
        embed["url"] = check["url"]
    if context:
        embed["fields"] += [
            {"name": "Alert rule", "value": str(context["rule_name"])[:1024], "inline": True},
            {"name": "Failures", "value": f"{_plural(context['failures'], 'failure')} in {context['window_minutes']} min", "inline": True},
            {"name": "Fires at", "value": f"{context['threshold']} in {context['window_minutes']} min", "inline": True},
        ]
    # Never let alert text ping anyone.
    return {"username": "guywatchingscreen", "allowed_mentions": {"parse": []}, "embeds": [embed]}


def teams_payload(check, message, detail=None, context=None):
    facts = [{"title": "URL", "value": check["url"]}]
    if context:
        facts += [
            {"title": "Alert rule", "value": str(context["rule_name"])},
            {"title": "Failures", "value": f"{_plural(context['failures'], 'failure')} in {context['window_minutes']} min"},
        ]
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": alert_title(check), "weight": "Bolder", "size": "Medium", "color": "Attention", "wrap": True},
            {"type": "TextBlock", "text": detail or message, "wrap": True},
            {"type": "FactSet", "facts": facts},
        ],
    }
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}


def json_payload(check, message, detail=None, context=None):
    text = webhook_text(check, message, detail, context)
    return {
        "text": text,
        "content": text[:2000],
        "event": "monitor.down",
        "title": f"{check['name']} is down",
        "message": message,
        "error": detail,
        "monitor": {"id": check["id"], "name": check["name"], "url": check["url"]},
        "rule": {
            "name": context["rule_name"],
            "failures": context["failures"],
            "window_minutes": context["window_minutes"],
            "threshold": context["threshold"],
        } if context else None,
        "timestamp": _timestamp(),
    }


def send_webhook_alert(check, message, detail=None, context=None):
    """POST the alert to WEBHOOK_URL. Returns True when sent, False when no webhook is set."""
    url = get_webhook_url()
    if not url:
        return False
    if not url.startswith(("http://", "https://")):
        raise ValueError("WEBHOOK_URL must start with http:// or https://")

    kind = webhook_kind(url)
    headers = {"User-Agent": "guywatchingscreen"}
    if kind == "discord":
        response = requests.post(url, json=discord_payload(check, message, detail, context), headers=headers, timeout=10)
    elif kind == "slack":
        response = requests.post(url, json={"text": webhook_text(check, message, detail, context)}, headers=headers, timeout=10)
    elif kind == "teams":
        response = requests.post(url, json=teams_payload(check, message, detail, context), headers=headers, timeout=10)
    elif kind == "ntfy":
        # ntfy shows the body as the notification and reads the title, priority and tags from headers.
        response = requests.post(
            url,
            data=webhook_text(check, message, detail, context).encode("utf-8"),
            headers={
                **headers,
                "Content-Type": "text/plain; charset=utf-8",
                "Title": f"{check['name']} is down".encode("ascii", "replace").decode(),
                "Priority": "high",
                "Tags": "red_circle",
                "Click": check["url"].encode("ascii", "ignore").decode(),
            },
            timeout=10,
        )
    else:
        response = requests.post(url, json=json_payload(check, message, detail, context), headers=headers, timeout=10)
    response.raise_for_status()
    return True


# --- API routes ---

@app.route("/api/communication")
def api_communication():
    return jsonify({"webhook": get_webhook_settings()})
