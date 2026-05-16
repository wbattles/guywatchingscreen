# Site Monitor

Simple HTTP and HTTPS monitoring.

## What it does

- checks websites on a schedule
- expects a `200` response
- tracks successes and failures
- supports blackout time windows
- alerts after `X` failures in `Y` minutes
- shows checks on one dashboard
- lets you edit each check

## Stack

- Python
- Flask
- SQLite
- APScheduler

## Run it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`

## Optional email alerts

Set these environment variables if you want email delivery:

```bash
export SMTP_HOST="smtp.example.com"
export SMTP_PORT="587"
export SMTP_USER="user"
export SMTP_PASSWORD="password"
export ALERT_TO="you@example.com"
export ALERT_FROM="monitor@example.com"
```

Without those, alerts still appear on the dashboard.

## Blackout periods

Use one time range per line:

```text
23:00-06:00
12:30-13:00
```

## Notes

- This app treats anything other than `200` as a failure.
- Checks run in the background every 30 seconds and only fire when due.
