# guywatchingscreen

Simple HTTP and HTTPS monitoring.

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

- email settings live on the **Communication** page
- put in the alert email address there
- put in the SMTP sender settings there too
- no startup environment variables are needed for email

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
