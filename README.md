```
 ██████╗ ██╗    ██╗███████╗
██╔════╝ ██║    ██║██╔════╝
██║  ███╗██║ █╗ ██║███████╗
██║   ██║██║███╗██║╚════██║
╚██████╔╝╚███╔███╔╝███████║
 ╚═════╝  ╚══╝╚══╝ ╚══════╝
```

# guywatchingscreen

[![Casino funds](https://img.shields.io/badge/Casino_funds-Ko--fi-ff5f5f?logo=ko-fi&logoColor=white)](https://ko-fi.com/wbattles)

Simple HTTP and HTTPS monitoring.

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

## Email

Set these in `.env` to send email alerts:

```env
SECRET_KEY=change-me
SENDER_EMAIL=alerts@example.com
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=alerts@example.com
SMTP_USE_TLS=1
SMTP_PASSWORD=change-me
```

Then add recipients on the comms tab.

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
