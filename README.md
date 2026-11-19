# guywatchingscreen

[![Casino funds](https://img.shields.io/badge/Casino_funds-Ko--fi-ff5f5f?logo=ko-fi&logoColor=white)](https://ko-fi.com/wbattles)

Like a guy watching some screens.

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

Open http://127.0.0.1:5001

Data persists in `./data/`.

## Webhook

Alerts always show on the dashboard. Set `WEBHOOK_URL` to also post every alert an alert rule raises to Discord, Slack, ntfy or any URL:

```env
WEBHOOK_URL=https://discord.com/api/webhooks/<id>/<token>
```

- Discord and Slack webhook URLs get their native JSON format
- any other URL (ntfy, n8n, Home Assistant, ...) gets a plain-text POST with a `Title` header
- the URL can also be mounted as a file at `/run/secrets/webhook_url`
- the URL is never stored in the database or returned by the API, since it contains a token

## Alerts

- alert rules use `X failures in Y minutes`
- failures are counted in a rolling time window
- a monitor stays marked down until the count drops below the rule

## Blackout periods

One time range per line:

```text
23:00-06:00
12:30-13:00
```

## License

MIT

## Releases

Releases are cut automatically from the squash-merged PR title (conventional commits):

- `fix: ...` → patch, `feat: ...` → minor, `feat!: ...` or a `BREAKING CHANGE:` footer → major
- `docs:`, `chore:`, `ci:`, `test:`, `refactor:`, `build:`, `perf:` → no release

Each release tags `vX.Y.Z`, creates a GitHub release, and pushes `ghcr.io/wbattles/guywatchingscreen:vX.Y.Z` (and `:latest`).
To release an exact version: `gh workflow run release.yaml --ref main -f version=X.Y.Z`.
