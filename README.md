# guywatchingscreen

Simple HTTP and HTTPS monitoring.

License: MIT

## What it does

- checks websites on a schedule
- expects a `200` response
- tracks success and failure counts from the last hour
- supports blackout time windows
- lets you edit each website
- has separate alert rules
- lets one alert rule apply to multiple monitors
- has a communication page for email settings
- shows recent alerts on the dashboard

## Run it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env with your real SMTP values
python app.py
```

Open `http://127.0.0.1:5000`

## Alerts

- alerts are configured on the **Alerts** page
- alert rules use `X failures in Y minutes`
- failures are counted in a rolling time window
- successes do not reset the count
- a monitor stays marked down until the rolling failure count drops below the rule

## Communication

- Email configuration is read from environment variables or Docker secrets. The **Communication** page now only shows the current settings and the list of recipient emails.
- For normal local Python runs, the app will also load variables from a local `.env` file automatically.
- Set the following environment variables (or provide Docker secrets) before starting the app:
  - `SENDER_EMAIL` – address used in the **From** field
  - `SMTP_HOST` – SMTP server hostname
  - `SMTP_PORT` – SMTP server port (default `587`)
  - `SMTP_USER` – optional username for authentication
  - `SMTP_USE_TLS` – `1` to enable TLS (default) or `0` to disable
  - `SMTP_PASSWORD` – password for the SMTP server (can be provided via a Docker secret named `smtp_password`)
- Recipients are still managed through the UI on the **Communication** page.
- If `SMTP_PORT` or `SMTP_USE_TLS` are missing or invalid, the app falls back to safe defaults.

Example `.env`:

```env
SENDER_EMAIL=alerts@example.com
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=alerts@example.com
SMTP_USE_TLS=1
SMTP_PASSWORD=change-me
```

## Blackout periods

Use one time range per line:

```text
23:00-06:00
12:30-13:00
```

## Notes

- this app treats anything other than `200` as a failure
- redirects are treated as failures
- checks run in the background every 30 seconds and only fire when due
- old check results are pruned automatically

## Docker

Build and run with Docker Compose:

```bash
# Build the image and start the container
docker compose up --build
```

Docker Compose serves the app on `http://127.0.0.1:5001`.

This compose file uses environment variables by default, including `SMTP_PASSWORD`.
If you want to use a Docker secret instead, mount a secret file to `/run/secrets/smtp_password`.

Example secret file:

```bash
mkdir -p secrets
printf '%s' 'your-real-password' > secrets/smtp_password.txt
```

Then uncomment the `secrets` lines in `docker-compose.yml`.

## Open source

- License: `LICENSE`
- Funding: `.github/FUNDING.yml`
- Code owners: `.github/CODEOWNERS`
- Workflows: `.github/workflows/`
- Branch rules: `.github/BRANCH_RULES.md`
