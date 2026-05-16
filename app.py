import os
import smtplib
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, flash, g, redirect, render_template, request, url_for


BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "monitor.db"
EXPECTED_STATUS = 200
SCHEDULER = BackgroundScheduler(daemon=True)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


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
            """
        )
        cursor.execute("PRAGMA table_info(checks)")
        check_columns = {row[1] for row in cursor.fetchall()}
        if "alert_failures" not in check_columns:
            cursor.execute("ALTER TABLE checks ADD COLUMN alert_failures INTEGER NOT NULL DEFAULT 3")
        if "alert_window_minutes" not in check_columns:
            cursor.execute("ALTER TABLE checks ADD COLUMN alert_window_minutes INTEGER NOT NULL DEFAULT 15")
        if "alert_active" not in check_columns:
            cursor.execute("ALTER TABLE checks ADD COLUMN alert_active INTEGER NOT NULL DEFAULT 0")

        cursor.execute("PRAGMA table_info(alerts)")
        alert_columns = {row[1] for row in cursor.fetchall()}
        if "alert_rule_id" not in alert_columns:
            cursor.execute("ALTER TABLE alerts ADD COLUMN alert_rule_id INTEGER")
        if "detail" not in alert_columns:
            cursor.execute("ALTER TABLE alerts ADD COLUMN detail TEXT")

        db.commit()
    db.close()


def now_utc():
    return datetime.now().replace(microsecond=0)


def iso(dt):
    return dt.isoformat()


def parse_iso(value):
    return datetime.fromisoformat(value) if value else None


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


def validate_check_form(form):
    name = form.get("name", "").strip()
    url = form.get("url", "").strip()
    frequency_minutes = int(form.get("frequency_minutes", 5))
    timeout_seconds = int(form.get("timeout_seconds", 10))
    blackout_periods = form.get("blackout_periods", "").strip()

    if not name:
        raise ValueError("Name is required.")
    if not url.startswith("http://") and not url.startswith("https://"):
        raise ValueError("URL must start with http:// or https://")
    if frequency_minutes < 1:
        raise ValueError("Frequency must be at least 1 minute.")
    if timeout_seconds < 1:
        raise ValueError("Timeout must be at least 1 second.")

    parse_blackout_periods(blackout_periods)

    return {
        "name": name,
        "url": url,
        "frequency_minutes": frequency_minutes,
        "timeout_seconds": timeout_seconds,
        "blackout_periods": blackout_periods,
    }


def validate_alert_form(form):
    name = form.get("name", "").strip()
    alert_failures = int(form.get("alert_failures", 3))
    alert_window_minutes = int(form.get("alert_window_minutes", 15))
    check_ids = sorted({int(value) for value in form.getlist("check_ids") if value.isdigit()})

    if not name:
        raise ValueError("Name is required.")
    if len(name) > 40:
        raise ValueError("Name must be 40 characters or fewer.")
    if alert_failures < 1:
        raise ValueError("Alert failures must be at least 1.")
    if alert_window_minutes < 1:
        raise ValueError("Alert window must be at least 1 minute.")

    return {
        "name": name,
        "alert_failures": alert_failures,
        "alert_window_minutes": alert_window_minutes,
        "check_ids": check_ids,
    }


def fetch_check(check_id):
    db = get_db()
    return db.execute("SELECT * FROM checks WHERE id = ?", (check_id,)).fetchone()


def send_email_alert(check, message):
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    alert_to = os.environ.get("ALERT_TO")
    alert_from = os.environ.get("ALERT_FROM", smtp_user or "alerts@example.com")

    if not smtp_host or not alert_to:
        return "dashboard"

    email = EmailMessage()
    email["Subject"] = f"Monitor alert: {check['name']}"
    email["From"] = alert_from
    email["To"] = alert_to
    email.set_content(message)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
        server.starttls()
        if smtp_user and smtp_password:
            server.login(smtp_user, smtp_password)
        server.send_message(email)

    return "email"


def create_alert(db, check, alert_type, message, alert_rule_id=None, detail=None):
    delivered_via = "dashboard"
    try:
        delivered_via = send_email_alert(check, message)
    except Exception as exc:
        message = f"{message} (email send failed: {exc})"

    db.execute(
        """
        INSERT INTO alerts (check_id, alert_rule_id, created_at, alert_type, message, detail, delivered_via)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (check["id"], alert_rule_id, iso(now_utc()), alert_type, message, detail, delivered_via),
    )


def failure_count_within_window(db, check_id, window_minutes):
    window_start = now_utc() - timedelta(minutes=window_minutes)
    row = db.execute(
        """
        SELECT COUNT(*) AS failure_count
        FROM check_results
        WHERE check_id = ?
          AND success = 0
          AND checked_at >= ?
        """,
        (check_id, iso(window_start)),
    ).fetchone()
    return row["failure_count"]


def alert_rules_for_check(db, check_id):
    return db.execute(
        """
        SELECT ar.*,
               COALESCE(ars.alert_active, 0) AS alert_active
        FROM alert_rules ar
        JOIN alert_rule_checks arc ON arc.alert_rule_id = ar.id
        LEFT JOIN alert_rule_states ars
          ON ars.alert_rule_id = ar.id AND ars.check_id = arc.check_id
        WHERE arc.check_id = ?
        ORDER BY ar.name COLLATE NOCASE ASC
        """,
        (check_id,),
    ).fetchall()


def set_alert_rule_state(db, alert_rule_id, check_id, is_active):
    db.execute(
        """
        INSERT INTO alert_rule_states (alert_rule_id, check_id, alert_active)
        VALUES (?, ?, ?)
        ON CONFLICT(alert_rule_id, check_id)
        DO UPDATE SET alert_active = excluded.alert_active
        """,
        (alert_rule_id, check_id, int(is_active)),
    )


def record_result(check_id, success, status_code=None, error_message=None, response_time_ms=None):
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    check = db.execute("SELECT * FROM checks WHERE id = ?", (check_id,)).fetchone()
    if check is None:
        db.close()
        return

    checked_at = now_utc()
    next_run_at = checked_at + timedelta(minutes=check["frequency_minutes"])

    db.execute(
        """
        INSERT INTO check_results (check_id, checked_at, success, status_code, error_message, response_time_ms)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (check_id, iso(checked_at), int(success), status_code, error_message, response_time_ms),
    )

    db.execute(
        """
        UPDATE checks
        SET last_checked_at = ?,
            next_run_at = ?,
            last_status_code = ?,
            last_error = ?,
            last_response_time_ms = ?
        WHERE id = ?
        """,
        (iso(checked_at), iso(next_run_at), status_code, error_message, response_time_ms, check_id),
    )

    refreshed_check = db.execute("SELECT * FROM checks WHERE id = ?", (check_id,)).fetchone()

    alert_rules = alert_rules_for_check(db, check_id)
    any_active = False
    for alert_rule in alert_rules:
        failures = failure_count_within_window(db, check_id, alert_rule["alert_window_minutes"])
        threshold_hit = failures >= alert_rule["alert_failures"]
        if threshold_hit:
            any_active = True
            if not alert_rule["alert_active"]:
                message = (
                    f"{refreshed_check['name']} failed {failures} times in the last "
                    f"{alert_rule['alert_window_minutes']} minutes for alert {alert_rule['name']}. "
                    f"Last status: {status_code or 'error'}."
                )
                detail = refreshed_check["last_error"] or f"HTTP {status_code}"
                create_alert(db, refreshed_check, "failure", message, alert_rule["id"], detail)
                set_alert_rule_state(db, alert_rule["id"], check_id, True)
        elif alert_rule["alert_active"]:
            set_alert_rule_state(db, alert_rule["id"], check_id, False)

    db.execute("UPDATE checks SET alert_active = ? WHERE id = ?", (int(any_active), check_id))

    db.commit()
    db.close()


def run_check(check_id):
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    check = db.execute("SELECT * FROM checks WHERE id = ?", (check_id,)).fetchone()
    db.close()
    if check is None:
        return
    if is_in_blackout(check["blackout_periods"]):
        return

    started = time.perf_counter()
    try:
        response = requests.get(check["url"], timeout=check["timeout_seconds"], allow_redirects=False)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        success = response.status_code == EXPECTED_STATUS
        error_message = None if success else f"Expected 200, got {response.status_code}"
        record_result(
            check_id,
            success=success,
            status_code=response.status_code,
            error_message=error_message,
            response_time_ms=elapsed_ms,
        )
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        record_result(
            check_id,
            success=False,
            error_message=str(exc),
            response_time_ms=elapsed_ms,
        )


def run_pending_checks():
    db = sqlite3.connect(DATABASE)
    db.row_factory = sqlite3.Row
    current_time = now_utc()
    checks = db.execute(
        """
        SELECT id FROM checks
        WHERE next_run_at IS NULL OR next_run_at <= ?
        ORDER BY COALESCE(next_run_at, created_at)
        """,
        (iso(current_time),),
    ).fetchall()
    db.close()

    for check in checks:
        run_check(check["id"])


def dashboard_data():
    db = get_db()
    window_start = iso(now_utc() - timedelta(hours=1))
    checks = db.execute(
        """
        SELECT
            c.*,
            COALESCE(rule_counts.alert_rule_count, 0) AS alert_rule_count,
            COALESCE(stats.success_count, 0) AS success_count,
            COALESCE(stats.failure_count, 0) AS failure_count
        FROM checks c
        LEFT JOIN (
            SELECT check_id, COUNT(*) AS alert_rule_count
            FROM alert_rule_checks
            GROUP BY check_id
        ) rule_counts ON rule_counts.check_id = c.id
        LEFT JOIN (
            SELECT
                check_id,
                SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count,
                SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failure_count
            FROM check_results
            WHERE checked_at >= ?
            GROUP BY check_id
        ) stats ON stats.check_id = c.id
        ORDER BY c.name COLLATE NOCASE ASC
        """
        ,
        (window_start,),
    ).fetchall()
    alerts = db.execute(
        """
        SELECT a.*, c.name AS check_name
        FROM alerts a
        JOIN checks c ON c.id = a.check_id
        ORDER BY a.created_at DESC
        LIMIT 20
        """
    ).fetchall()
    return checks, alerts


def alert_settings_data():
    db = get_db()
    return db.execute(
        """
        SELECT ar.id,
               ar.name,
               ar.alert_failures,
               ar.alert_window_minutes,
               COUNT(arc.check_id) AS monitor_count,
               GROUP_CONCAT(c.name, ', ') AS monitor_names
        FROM alert_rules ar
        LEFT JOIN alert_rule_checks arc ON arc.alert_rule_id = ar.id
        LEFT JOIN checks c ON c.id = arc.check_id
        GROUP BY ar.id, ar.name, ar.alert_failures, ar.alert_window_minutes
        ORDER BY ar.name COLLATE NOCASE ASC
        """
    ).fetchall()


def checks_for_forms():
    db = get_db()
    return db.execute("SELECT id, name FROM checks ORDER BY name COLLATE NOCASE ASC").fetchall()


def fetch_alert_rule(alert_rule_id):
    db = get_db()
    return db.execute("SELECT * FROM alert_rules WHERE id = ?", (alert_rule_id,)).fetchone()


def fetch_alert(alert_id):
    db = get_db()
    return db.execute(
        """
        SELECT a.*, c.name AS check_name, c.url AS check_url, ar.name AS alert_rule_name
        FROM alerts a
        JOIN checks c ON c.id = a.check_id
        LEFT JOIN alert_rules ar ON ar.id = a.alert_rule_id
        WHERE a.id = ?
        """,
        (alert_id,),
    ).fetchone()


def alert_rule_check_ids(db, alert_rule_id):
    rows = db.execute("SELECT check_id FROM alert_rule_checks WHERE alert_rule_id = ?", (alert_rule_id,)).fetchall()
    return [row["check_id"] for row in rows]


@app.route("/")
def dashboard():
    checks, alerts = dashboard_data()
    return render_template(
        "dashboard.html",
        checks=checks,
        alerts=alerts,
        expected_status=EXPECTED_STATUS,
    )


@app.route("/checks/new", methods=["GET", "POST"])
def create_check():
    if request.method == "POST":
        try:
            data = validate_check_form(request.form)
            timestamp = iso(now_utc())
            db = get_db()
            db.execute(
                """
                INSERT INTO checks (
                    name, url, frequency_minutes, timeout_seconds, blackout_periods,
                    alert_failures, alert_window_minutes, created_at, next_run_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["name"],
                    data["url"],
                    data["frequency_minutes"],
                    data["timeout_seconds"],
                    data["blackout_periods"],
                    3,
                    15,
                    timestamp,
                    timestamp,
                ),
            )
            db.commit()
            flash("Check created.", "success")
            return redirect(url_for("dashboard"))
        except ValueError as exc:
            flash(str(exc), "error")

    return render_template("check_form.html", check=None)


@app.route("/checks/<int:check_id>/edit", methods=["GET", "POST"])
def edit_check(check_id):
    check = fetch_check(check_id)
    if check is None:
        flash("Check not found.", "error")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        try:
            data = validate_check_form(request.form)
            db = get_db()
            db.execute(
                """
                UPDATE checks
                SET name = ?,
                    url = ?,
                    frequency_minutes = ?,
                    timeout_seconds = ?,
                    blackout_periods = ?
                WHERE id = ?
                """,
                (
                    data["name"],
                    data["url"],
                    data["frequency_minutes"],
                    data["timeout_seconds"],
                    data["blackout_periods"],
                    check_id,
                ),
            )
            db.commit()
            flash("Check updated.", "success")
            return redirect(url_for("dashboard"))
        except ValueError as exc:
            flash(str(exc), "error")
            check = dict(check)
            check.update(request.form)

    return render_template("check_form.html", check=check)


@app.post("/checks/<int:check_id>/run")
def trigger_check(check_id):
    if fetch_check(check_id) is None:
        flash("Check not found.", "error")
        return redirect(url_for("dashboard"))
    run_check(check_id)
    return redirect(url_for("dashboard"))


@app.post("/checks/<int:check_id>/delete")
def delete_check(check_id):
    db = get_db()
    check = db.execute("SELECT id FROM checks WHERE id = ?", (check_id,)).fetchone()
    if check is None:
        flash("Check not found.", "error")
        return redirect(url_for("dashboard"))

    db.execute("DELETE FROM alert_rule_states WHERE check_id = ?", (check_id,))
    db.execute("DELETE FROM alert_rule_checks WHERE check_id = ?", (check_id,))
    db.execute("DELETE FROM alerts WHERE check_id = ?", (check_id,))
    db.execute("DELETE FROM check_results WHERE check_id = ?", (check_id,))
    db.execute("DELETE FROM checks WHERE id = ?", (check_id,))
    db.commit()
    flash("Website deleted.", "success")
    return redirect(url_for("dashboard"))


@app.route("/alerts")
def alerts_screen():
    alert_settings = alert_settings_data()
    return render_template("alerts.html", alert_settings=alert_settings)


@app.route("/alerts/<int:alert_id>")
def alert_detail(alert_id):
    alert = fetch_alert(alert_id)
    if alert is None:
        flash("Alert not found.", "error")
        return redirect(url_for("dashboard"))
    return render_template("alert_detail.html", alert=alert)


@app.post("/alerts/clear")
def clear_alerts():
    db = get_db()
    db.execute("DELETE FROM alerts")
    db.commit()
    flash("Alerts cleared.", "success")
    return redirect(url_for("dashboard"))


@app.post("/alerts/<int:alert_id>/delete")
def delete_alert(alert_id):
    db = get_db()
    db.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
    db.commit()
    flash("Alert deleted.", "success")
    return redirect(url_for("dashboard"))


@app.route("/alerts/new", methods=["GET", "POST"])
def create_alert_rule():
    db = get_db()
    checks = checks_for_forms()
    selected_check_ids = []

    if request.method == "POST":
        selected_check_ids = [int(value) for value in request.form.getlist("check_ids") if value.isdigit()]
        try:
            data = validate_alert_form(request.form)
            db.execute(
                """
                INSERT INTO alert_rules (name, alert_failures, alert_window_minutes, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (data["name"], data["alert_failures"], data["alert_window_minutes"], iso(now_utc())),
            )
            alert_rule_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            for check_id in data["check_ids"]:
                db.execute(
                    "INSERT INTO alert_rule_checks (alert_rule_id, check_id) VALUES (?, ?)",
                    (alert_rule_id, check_id),
                )
            db.commit()
            flash("Alert created.", "success")
            return redirect(url_for("alerts_screen"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template("alert_form.html", checks=checks, alert_rule=None, selected_check_ids=selected_check_ids)


@app.route("/alerts/<int:alert_rule_id>/edit", methods=["GET", "POST"])
def edit_alert_rule(alert_rule_id):
    db = get_db()
    checks = checks_for_forms()
    alert_rule = fetch_alert_rule(alert_rule_id)
    if alert_rule is None:
        flash("Alert not found.", "error")
        return redirect(url_for("alerts_screen"))

    if request.method == "POST":
        selected_check_ids = [int(value) for value in request.form.getlist("check_ids") if value.isdigit()]
        try:
            data = validate_alert_form(request.form)
            db.execute(
                """
                UPDATE alert_rules
                SET name = ?, alert_failures = ?, alert_window_minutes = ?
                WHERE id = ?
                """,
                (data["name"], data["alert_failures"], data["alert_window_minutes"], alert_rule_id),
            )
            db.execute("DELETE FROM alert_rule_checks WHERE alert_rule_id = ?", (alert_rule_id,))
            for check_id in data["check_ids"]:
                db.execute(
                    "INSERT INTO alert_rule_checks (alert_rule_id, check_id) VALUES (?, ?)",
                    (alert_rule_id, check_id),
                )
            if data["check_ids"]:
                db.execute(
                    "DELETE FROM alert_rule_states WHERE alert_rule_id = ? AND check_id NOT IN ({})".format(
                        ",".join("?" * len(data["check_ids"]))
                    ),
                    [alert_rule_id, *data["check_ids"]],
                )
            else:
                db.execute("DELETE FROM alert_rule_states WHERE alert_rule_id = ?", (alert_rule_id,))
            db.commit()
            flash("Alert updated.", "success")
            return redirect(url_for("alerts_screen"))
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template(
                "alert_form.html",
                checks=checks,
                alert_rule=alert_rule,
                selected_check_ids=selected_check_ids,
            )

    selected_check_ids = alert_rule_check_ids(db, alert_rule_id)
    return render_template(
        "alert_form.html",
        checks=checks,
        alert_rule=alert_rule,
        selected_check_ids=selected_check_ids,
    )


@app.post("/alert-rules/<int:alert_rule_id>/delete")
def delete_alert_rule(alert_rule_id):
    db = get_db()
    alert_rule = db.execute("SELECT id FROM alert_rules WHERE id = ?", (alert_rule_id,)).fetchone()
    if alert_rule is None:
        flash("Alert not found.", "error")
        return redirect(url_for("alerts_screen"))

    db.execute("DELETE FROM alert_rule_states WHERE alert_rule_id = ?", (alert_rule_id,))
    db.execute("DELETE FROM alert_rule_checks WHERE alert_rule_id = ?", (alert_rule_id,))
    db.execute("DELETE FROM alerts WHERE alert_rule_id = ?", (alert_rule_id,))
    db.execute("DELETE FROM alert_rules WHERE id = ?", (alert_rule_id,))
    db.commit()
    flash("Alert deleted.", "success")
    return redirect(url_for("alerts_screen"))


def start_scheduler():
    if not SCHEDULER.running:
        SCHEDULER.add_job(run_pending_checks, "interval", seconds=30, id="pending-checks", replace_existing=True)
        SCHEDULER.start()


init_db()
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
    start_scheduler()


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=True, host=host, port=port)
