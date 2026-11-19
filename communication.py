import os
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import jsonify, request

from common import app, get_db, get_json_object, iso, now_utc


# --- Webhooks ---
# Webhooks are added on the Comms tab (stored in the database); WEBHOOK_URL from the environment
# is one more. Each alert rule picks which of them it sends to, or none for dashboard only.
# A webhook URL is its credential, so URLs are write-only: the API accepts them but only
# ever returns a masked hint, never the full URL.

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


def webhook_kind(url, fmt=None):
    """An explicit format wins (a webhook's own setting, else WEBHOOK_FORMAT); otherwise guess from the URL."""
    forced = (fmt if fmt not in (None, "", "auto") else os.getenv("WEBHOOK_FORMAT", "")).strip().lower()
    if forced in WEBHOOK_FORMATS:
        return forced
    return detect_webhook_format(url)


def mask_url(url):
    """Enough to recognise a webhook (host and last 4 characters) without exposing its token."""
    parsed = urlparse(url)
    tail = url.rstrip("/")[-4:]
    return f"{parsed.hostname or '?'}/…{tail}"


def env_webhook():
    url = get_webhook_url()
    if not url:
        return None
    return {"id": None, "name": "Environment (WEBHOOK_URL)", "url": url, "format": "auto", "enabled": 1, "source": "environment"}


def webhook_targets(db, include_disabled=False):
    rows = db.execute("SELECT * FROM webhooks ORDER BY name COLLATE NOCASE ASC, id ASC").fetchall()
    targets = [{**dict(r), "source": "settings"} for r in rows if include_disabled or r["enabled"]]
    env = env_webhook()
    if env:
        targets.append(env)
    return targets


def public_webhook(target):
    return {
        "id": target["id"],
        "name": target["name"],
        "url_hint": mask_url(target["url"]),
        "format": target["format"],
        "kind": webhook_kind(target["url"], target["format"]),
        "enabled": bool(target["enabled"]),
        "source": target["source"],
    }


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


def post_webhook(url, fmt, check, message, detail=None, context=None):
    """POST one alert to one webhook URL in the format it expects. Raises on any failure."""
    if not url.startswith(("http://", "https://")):
        raise ValueError("Webhook URL must start with http:// or https://")

    kind = webhook_kind(url, fmt)
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


def rule_webhook_targets(db, alert_rule_id, include_disabled=False):
    """The webhooks an alert rule sends to: its selected webhooks, plus WEBHOOK_URL if it opted in."""
    rows = db.execute(
        """
        SELECT w.*
        FROM webhooks w
        JOIN alert_rule_webhooks arw ON arw.webhook_id = w.id
        WHERE arw.alert_rule_id = ?
        ORDER BY w.name COLLATE NOCASE ASC, w.id ASC
        """,
        (alert_rule_id,),
    ).fetchall()
    targets = [{**dict(r), "source": "settings"} for r in rows if include_disabled or r["enabled"]]
    rule = db.execute("SELECT notify_environment FROM alert_rules WHERE id = ?", (alert_rule_id,)).fetchone()
    env = env_webhook()
    if env and rule is not None and rule["notify_environment"]:
        targets.append(env)
    return targets


def send_webhook_alerts(db, alert_rule_id, check, message, detail=None, context=None):
    """Send to the rule's enabled webhooks. Returns (sent, failed) lists of webhook names."""
    sent, failed = [], []
    for target in rule_webhook_targets(db, alert_rule_id):
        try:
            post_webhook(target["url"], target["format"], check, message, detail, context)
            sent.append(target["name"])
        except Exception as exc:
            # Log only the exception type: request errors can include the webhook URL and its token.
            app.logger.error(
                "Failed to send alert webhook %r for check %s (%s)",
                target["name"],
                check["id"],
                type(exc).__name__,
            )
            failed.append(target["name"])
    return sent, failed


def validate_webhook_json(data, require_url=True):
    name = (data.get("name") or "").strip()
    url = (data.get("url") or "").strip()
    fmt = (data.get("format") or "auto").strip().lower()
    enabled = data.get("enabled", True)

    if not name:
        raise ValueError("Name is required.")
    if len(name) > 40:
        raise ValueError("Name must be 40 characters or fewer.")
    if url or require_url:
        if not url.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        if len(url) > 2000:
            raise ValueError("URL is too long.")
    if fmt not in ("auto", *WEBHOOK_FORMATS):
        raise ValueError(f"Format must be one of: auto, {', '.join(WEBHOOK_FORMATS)}.")
    return {"name": name, "url": url, "format": fmt, "enabled": 1 if enabled else 0}


def test_alert(target):
    check = {"id": 0, "name": "Test monitor", "url": "https://example.com/health"}
    message = f"Test alert from guywatchingscreen to \"{target['name']}\". If you can see this, the webhook works."
    post_webhook(target["url"], target["format"], check, message, "This is a test alert. Nothing is down.")


# --- API routes ---

@app.route("/api/communication")
def api_communication():
    return jsonify({"webhooks": [public_webhook(t) for t in webhook_targets(get_db(), include_disabled=True)]})


def _fetch_webhook(db, webhook_id):
    return db.execute("SELECT * FROM webhooks WHERE id = ?", (webhook_id,)).fetchone()


@app.route("/api/webhooks", methods=["POST"])
def api_create_webhook():
    try:
        data = validate_webhook_json(get_json_object(request))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    db = get_db()
    cursor = db.execute(
        "INSERT INTO webhooks (name, url, format, enabled, created_at) VALUES (?, ?, ?, ?, ?)",
        (data["name"], data["url"], data["format"], data["enabled"], iso(now_utc())),
    )
    db.commit()
    return jsonify({"ok": True, "id": cursor.lastrowid}), 201


@app.route("/api/webhooks/<int:webhook_id>", methods=["PUT"])
def api_update_webhook(webhook_id):
    db = get_db()
    existing = _fetch_webhook(db, webhook_id)
    if existing is None:
        return jsonify({"error": "Webhook not found."}), 404
    try:
        # A blank URL keeps the current one, since the page never sees it.
        data = validate_webhook_json(get_json_object(request), require_url=False)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    db.execute(
        "UPDATE webhooks SET name = ?, url = ?, format = ?, enabled = ? WHERE id = ?",
        (data["name"], data["url"] or existing["url"], data["format"], data["enabled"], webhook_id),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/webhooks/<int:webhook_id>", methods=["DELETE"])
def api_delete_webhook(webhook_id):
    db = get_db()
    if _fetch_webhook(db, webhook_id) is None:
        return jsonify({"error": "Webhook not found."}), 404
    db.execute("DELETE FROM alert_rule_webhooks WHERE webhook_id = ?", (webhook_id,))
    db.execute("DELETE FROM webhooks WHERE id = ?", (webhook_id,))
    db.commit()
    return jsonify({"ok": True})


def _send_test(target):
    try:
        test_alert(target)
        return jsonify({"ok": True})
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        reason = f"the webhook answered HTTP {status}" if status else type(exc).__name__
        return jsonify({"error": f"Test failed: {reason}."}), 502


@app.route("/api/webhooks/<int:webhook_id>/test", methods=["POST"])
def api_test_webhook(webhook_id):
    row = _fetch_webhook(get_db(), webhook_id)
    if row is None:
        return jsonify({"error": "Webhook not found."}), 404
    return _send_test(dict(row))


@app.route("/api/webhooks/environment/test", methods=["POST"])
def api_test_env_webhook():
    target = env_webhook()
    if target is None:
        return jsonify({"error": "WEBHOOK_URL isn't set."}), 404
    return _send_test(target)
