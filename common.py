import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from flask import Flask, g


BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "monitor.db"
EXPECTED_STATUS = 200

app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-secret-key"


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def open_db():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


@app.teardown_appcontext
def close_db(_error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE)
    with closing(db.cursor()) as cursor:
        cursor.executescript(
            """
            CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                frequency_minutes INTEGER NOT NULL,
                timeout_seconds INTEGER NOT NULL DEFAULT 10,
                blackout_periods TEXT NOT NULL DEFAULT '',
                alert_active INTEGER NOT NULL DEFAULT 0,
                last_checked_at TEXT,
                next_run_at TEXT,
                last_status_code INTEGER,
                last_error TEXT,
                last_response_time_ms INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS check_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                check_id INTEGER NOT NULL,
                checked_at TEXT NOT NULL,
                success INTEGER NOT NULL,
                status_code INTEGER,
                error_message TEXT,
                response_time_ms INTEGER,
                FOREIGN KEY (check_id) REFERENCES checks (id)
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                check_id INTEGER NOT NULL,
                alert_rule_id INTEGER,
                created_at TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                message TEXT NOT NULL,
                detail TEXT,
                delivered_via TEXT NOT NULL DEFAULT 'dashboard',
                FOREIGN KEY (check_id) REFERENCES checks (id)
            );

            CREATE TABLE IF NOT EXISTS alert_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                alert_failures INTEGER NOT NULL,
                alert_window_minutes INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alert_rule_checks (
                alert_rule_id INTEGER NOT NULL,
                check_id INTEGER NOT NULL,
                PRIMARY KEY (alert_rule_id, check_id),
                FOREIGN KEY (alert_rule_id) REFERENCES alert_rules (id),
                FOREIGN KEY (check_id) REFERENCES checks (id)
            );

            CREATE TABLE IF NOT EXISTS alert_rule_states (
                alert_rule_id INTEGER NOT NULL,
                check_id INTEGER NOT NULL,
                alert_active INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (alert_rule_id, check_id),
                FOREIGN KEY (alert_rule_id) REFERENCES alert_rules (id),
                FOREIGN KEY (check_id) REFERENCES checks (id)
            );

            CREATE TABLE IF NOT EXISTS communication_email_recipients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alert_rule_email_recipients (
                alert_rule_id INTEGER NOT NULL,
                recipient_id INTEGER NOT NULL,
                PRIMARY KEY (alert_rule_id, recipient_id),
                FOREIGN KEY (alert_rule_id) REFERENCES alert_rules (id),
                FOREIGN KEY (recipient_id) REFERENCES communication_email_recipients (id)
            );

            CREATE INDEX IF NOT EXISTS idx_check_results_check_checked
                ON check_results (check_id, checked_at);

            CREATE INDEX IF NOT EXISTS idx_check_results_check_success_checked
                ON check_results (check_id, success, checked_at);

            CREATE INDEX IF NOT EXISTS idx_alerts_check_id
                ON alerts (check_id);

            CREATE INDEX IF NOT EXISTS idx_alerts_created_at
                ON alerts (created_at DESC);
            """
        )
        db.commit()
    db.close()


def now_local():
    return datetime.now().replace(microsecond=0)


def iso(dt):
    return dt.isoformat()


def parse_int_field(form, field_name, default, label):
    raw_value = form.get(field_name, default)
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a whole number.")


def looks_like_email(value):
    return bool(value) and "@" in value and "." in value.rsplit("@", 1)[-1]


def parse_blackout_periods(raw_text):
    periods = []
    for line in (raw_text or "").splitlines():
        value = line.strip()
        if not value:
            continue
        if "-" not in value:
            raise ValueError(f"Invalid blackout period: {value}")
        start_text, end_text = [part.strip() for part in value.split("-", 1)]
        start = datetime.strptime(start_text, "%H:%M").time()
        end = datetime.strptime(end_text, "%H:%M").time()
        periods.append((start, end))
    return periods


def is_in_blackout(raw_text, current_time=None):
    current_time = current_time or datetime.now().time()
    for start, end in parse_blackout_periods(raw_text):
        if start <= end:
            if start <= current_time <= end:
                return True
        else:
            if current_time >= start or current_time <= end:
                return True
    return False
