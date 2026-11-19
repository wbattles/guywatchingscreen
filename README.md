# guywatchingscreen

[![Casino funds](https://img.shields.io/badge/Casino_funds-Ko--fi-ff5f5f?logo=ko-fi&logoColor=white)](https://ko-fi.com/wbattles)

Like a guy watching some screens.

A small uptime monitor. It checks your URLs on a schedule, shows them on a dashboard, and posts alerts to Discord, Slack, Teams, ntfy or any other webhook when they go down.

- **Monitors** check a URL every 1 to 1440 minutes and pass only on HTTP 200
- **Alert rules** fire when a monitor fails X times within Y minutes
- **Webhook alerts** post to Discord (as an embed), Slack, Teams, ntfy or any URL
- **Blackout periods** pause a monitor during set hours
- **Light and dark themes**, plus a JSON API for everything the UI does

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Open http://127.0.0.1:5000

## Run with Docker

```bash
cp .env.example .env
docker compose up -d
```

Open http://127.0.0.1:5001. Data persists in `./data/`.

Images are published to `ghcr.io/wbattles/guywatchingscreen` for `linux/amd64` and `linux/arm64`.

## Configuration

All settings come from the environment (or `.env`).

| Variable | Default | What it does |
|---|---|---|
| `WEBHOOK_URL` | unset | A webhook to add to the Comms tab on startup. Most people add webhooks on the Comms tab instead. |
| `WEBHOOK_FORMAT` | picked from the URL | The format for the imported `WEBHOOK_URL`: `discord`, `slack`, `teams`, `ntfy` or `json`. |
| `SECRET_KEY` | `dev-secret-key` | Flask secret key. Set a random string in production. |
| `HOST` | `127.0.0.1` | Address to listen on. Use `0.0.0.0` in a container. |
| `PORT` | `5000` | Port to listen on. |
| `DATA_DIR` | the app folder | Where the SQLite database (`monitor.db`) lives. |
| `RUN_SCHEDULER` | on when run with `python app.py` | Set to `0` to serve the UI without running checks. |
| `FLASK_DEBUG` | off | Flask debug mode. Development only. |

## Webhook alerts

Alerts always show on the dashboard. To also send them somewhere, open the **Comms** tab and click **Add webhook**: give it a name, paste the webhook URL, and click **Send test**. Add as many as you like, such as one per Discord channel. Each can be edited, switched off or deleted.

Each alert rule picks which webhooks it sends to under **Send to**, or none for dashboard only.

Webhook URLs work like passwords, so they're write-only: once saved, the page and API only show a hint like `discord.com/…7tDl`. To change one, paste a new URL when editing; leaving it blank keeps the current one.

You can also set a webhook in the environment. On startup it's added to the Comms tab as a normal webhook (and to any rules that used it), so you can rename, edit or delete it there. It's only imported once; changing `WEBHOOK_URL` to a different URL imports the new one.

```env
WEBHOOK_URL=https://discord.com/api/webhooks/<id>/<token>
```

The format is picked from the URL:

| Webhook | What gets sent |
|---|---|
| Discord | A red embed titled "🔴 *monitor* is down" that links to the URL, with the error, the alert rule, the failure count and a timestamp. Mentions are disabled. |
| Slack, Google Chat, classic Teams connectors | `{"text": ...}` with the same details. |
| Teams Workflows (Power Automate) | An Adaptive Card. |
| ntfy (`ntfy.sh` or a host starting with `ntfy.`) | A high-priority push with a title, the details, and a tap-through link to the URL. |
| Anything else (Mattermost, Rocket.Chat, n8n, Home Assistant, Zapier, your own endpoint...) | JSON with `text` and `content` for chat apps, plus `title`, `error`, `monitor`, `rule` and `timestamp` fields for automations. |

The format is picked from the URL unless you choose one when adding the webhook, which is useful for something like a self-hosted ntfy on its own domain. For `WEBHOOK_URL`, set `WEBHOOK_FORMAT` to `discord`, `slack`, `teams`, `ntfy` or `json`.

`WEBHOOK_URL` can also be mounted as a file at `/run/secrets/webhook_url`.

**Getting a Discord webhook URL:** in your server, open a channel's settings, then **Integrations → Webhooks → New Webhook**, then **Copy Webhook URL**.

## How checks and alerts work

- A check passes only on **HTTP 200**. Redirects aren't followed, so point monitors at the final URL, such as a `/health` endpoint.
- Failures are written up in plain words, for example `Got HTTP 503 Service Unavailable, expected 200`, `No response within 10s (timed out)`, or `Couldn't find the host (DNS lookup failed)`.
- An alert rule fires when a monitor has **X failures within the last Y minutes**. It fires once, stays active while the count is at or above X, and resets when the count drops below.
- Monitor status:
  - **Up** if the last check passed.
  - **Down** if it failed and one of its rules is active, or it has no rules.
  - **Degraded** if it failed but no rule has fired yet.
- Check results are kept for 7 days.

## Blackout periods

Checks are skipped during blackout periods. Enter one range per line, in the server's local time:

```text
23:00-06:00
12:30-13:00
```

## API

Everything the UI does goes through a JSON API.

```bash
# Add a monitor (only name and url are required)
curl -X POST http://127.0.0.1:5000/api/checks \
  -H 'Content-Type: application/json' \
  -d '{"name": "My API", "url": "https://example.com/health", "frequency_minutes": 5, "timeout_seconds": 10}'

# Alert webhook 1 when this monitor fails once in 15 minutes
curl -X POST http://127.0.0.1:5000/api/alert-rules \
  -H 'Content-Type: application/json' \
  -d '{"name": "prod down", "alert_failures": 1, "alert_window_minutes": 15, "check_ids": [1], "webhook_ids": [1]}'
```

| Method and path | What it does |
|---|---|
| `GET /api/dashboard` | Every monitor with its status, plus recent alerts |
| `POST /api/checks` | Add a monitor: `name`, `url`, optional `frequency_minutes` (default 5), `timeout_seconds` (default 10), `blackout_periods` |
| `GET`, `PUT` or `DELETE /api/checks/<id>` | Read, replace or delete a monitor |
| `POST /api/checks/<id>/run` | Run a check now |
| `GET /api/alerts` | List alert rules |
| `POST /api/alert-rules` | Add a rule: `name`, `alert_failures` (default 3), `alert_window_minutes` (default 15), `check_ids`, `webhook_ids` (none means dashboard only), `notify_environment` (send to `WEBHOOK_URL`) |
| `GET`, `PUT` or `DELETE /api/alert-rules/<id>` | Read, replace or delete a rule |
| `GET` or `DELETE /api/alerts/<id>` | Read or delete one alert |
| `POST /api/alerts/clear` | Delete all alerts |
| `GET /api/communication` | Every webhook, with a masked URL hint |
| `POST /api/webhooks` | Add a webhook: `name`, `url`, optional `format` (default `auto`), `enabled` |
| `PUT` or `DELETE /api/webhooks/<id>` | Replace or delete a webhook (a blank `url` keeps the current one) |
| `POST /api/webhooks/<id>/test` | Send a test alert (`environment` for `WEBHOOK_URL`) |
| `GET /health` | Returns `OK` (for liveness and readiness probes) |

Create endpoints return `{"ok": true}`, and errors return `{"error": "..."}` with a 4xx status.

## Releases

Releases are cut automatically from the squash-merged PR title (conventional commits):

- `fix: ...` → patch, `feat: ...` → minor, `feat!: ...` or a `BREAKING CHANGE:` footer → major
- `docs:`, `chore:`, `ci:`, `test:`, `refactor:`, `build:`, `perf:` → no release

Each release tags `vX.Y.Z`, creates a GitHub release, and pushes `ghcr.io/wbattles/guywatchingscreen:vX.Y.Z` (and `:latest`).
To release an exact version: `gh workflow run release.yaml --ref main -f version=X.Y.Z`.

## License

MIT
