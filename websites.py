from datetime import timedelta
from http import HTTPStatus
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import jsonify, request

from alerts import alert_rules_for_check, create_alert, failure_count_within_window, set_alert_rule_state
from common import EXPECTED_STATUS, app, get_db, get_json_object, iso, is_in_blackout, now_utc, open_db, parse_blackout_periods, parse_int_field


SCHEDULER = BackgroundScheduler(daemon=True)


def describe_status(response):
    """Explain a non-200 response in plain words."""
    code = response.status_code
    try:
        phrase = HTTPStatus(code).phrase
    except ValueError:
        phrase = ""
    status = f"HTTP {code} {phrase}".strip()
    if 300 <= code < 400:
        location = response.headers.get("Location")
        where = f" to {location}" if location else ""
        return f"Redirected{where} ({status}); monitors expect {EXPECTED_STATUS} and don't follow redirects"
    return f"Got {status}, expected {EXPECTED_STATUS}"


def describe_exception(exc, timeout_seconds):
    """Turn a requests exception into a short, readable reason instead of the raw urllib3 chain."""
    text = str(exc)
    if isinstance(exc, requests.exceptions.Timeout):
        if isinstance(exc, requests.exceptions.ConnectTimeout):
            return f"Couldn't connect within {timeout_seconds}s (connection timed out)"
        return f"No response within {timeout_seconds}s (timed out)"
    if isinstance(exc, requests.exceptions.SSLError):
        if "CERTIFICATE_VERIFY_FAILED" in text:
            return "TLS certificate isn't valid (verification failed)"
        return "TLS/SSL handshake failed"
    if isinstance(exc, requests.exceptions.ConnectionError):
        lowered = text.lower()
        if any(s in lowered for s in ("nameresolution", "name or service not known", "nodename nor servname", "failed to resolve", "no address associated")):
            return "Couldn't find the host (DNS lookup failed)"
        if "connection refused" in lowered:
            return "Connection refused (nothing is listening there)"
        if "remotedisconnected" in lowered or "connection aborted" in lowered or "connection reset" in lowered:
            return "The server closed the connection without responding"
        return "Couldn't connect to the server"
    if isinstance(exc, (requests.exceptions.InvalidURL, requests.exceptions.MissingSchema)):
        return "The monitor's URL isn't valid"
    return f"{type(exc).__name__}: {text}"[:300]


def describe_alert(check, alert_rule, failures, reason):
    """One clear sentence for the dashboard, email and webhook: what is down, why, and which rule fired."""
    window = alert_rule["alert_window_minutes"]
    threshold = alert_rule["alert_failures"]
    plural = "failure" if failures == 1 else "failures"
    rule_plural = "failure" if threshold == 1 else "failures"
    return (
        f"{check['name']} is down: {reason}. "
        f"{failures} {plural} in the last {window} min; alert rule \"{alert_rule['name']}\" "
        f"fires at {threshold} {rule_plural}."
    )


def _safe_int(data, key, default, label):
    raw = data.get(key, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a whole number.")


def validate_check_json(data):
    name = (data.get("name") or "").strip()
    url = (data.get("url") or "").strip()
    frequency_minutes = _safe_int(data, "frequency_minutes", 5, "Frequency")
    timeout_seconds = _safe_int(data, "timeout_seconds", 10, "Timeout")
    blackout_periods = (data.get("blackout_periods") or "").strip()

    if not name:
        raise ValueError("Name is required.")
    if not url.startswith("http://") and not url.startswith("https://"):
        raise ValueError("URL must start with http:// or https://")
    if frequency_minutes < 1 or frequency_minutes > 1440:
        raise ValueError("Frequency must be between 1 and 1440 minutes.")
    if timeout_seconds < 1 or timeout_seconds > 60:
        raise ValueError("Timeout must be between 1 and 60 seconds.")

    parse_blackout_periods(blackout_periods)
    return {
        "name": name,
        "url": url,
        "frequency_minutes": frequency_minutes,
        "timeout_seconds": timeout_seconds,
        "blackout_periods": blackout_periods,
    }


def fetch_check(check_id):
    db = get_db()
    return db.execute("SELECT * FROM checks WHERE id = ?", (check_id,)).fetchone()


def run_check(check_id):
    db = open_db()
    try:
        check = db.execute("SELECT * FROM checks WHERE id = ?", (check_id,)).fetchone()
        if check is None:
            return
        if is_in_blackout(check["blackout_periods"]):
            next_run_at = now_utc() + timedelta(minutes=check["frequency_minutes"])
            db.execute("UPDATE checks SET next_run_at = ? WHERE id = ?", (iso(next_run_at), check_id))
            db.commit()
            return

        started = time.perf_counter()
        try:
            response = requests.get(check["url"], timeout=check["timeout_seconds"], allow_redirects=False)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            success = response.status_code == EXPECTED_STATUS
            error_message = None if success else describe_status(response)
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            success = False
            response = None
            error_message = describe_exception(exc, check["timeout_seconds"])

        status_code = response.status_code if response is not None else None
        checked_at = now_utc()
        next_run_at = checked_at + timedelta(minutes=check["frequency_minutes"])

        db.execute(
            """
            INSERT INTO check_results (check_id, checked_at, success, status_code, error_message, response_time_ms)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (check_id, iso(checked_at), int(success), status_code, error_message, elapsed_ms),
        )
        db.execute(
            """
            UPDATE checks
            SET last_checked_at = ?, next_run_at = ?, last_status_code = ?, last_error = ?, last_response_time_ms = ?
            WHERE id = ?
            """,
            (iso(checked_at), iso(next_run_at), status_code, error_message, elapsed_ms, check_id),
        )

        alert_rules = alert_rules_for_check(db, check_id)
        any_active = False
        pending_alerts = []

        for alert_rule in alert_rules:
            failures = failure_count_within_window(db, check_id, alert_rule["alert_window_minutes"])
            threshold_hit = failures >= alert_rule["alert_failures"]
            if threshold_hit:
                any_active = True
                if not alert_rule["alert_active"]:
                    reason = error_message or "the latest check passed, but earlier failures are still in the window"
                    pending_alerts.append({
                        "rule": alert_rule,
                        "failures": failures,
                        "message": describe_alert(check, alert_rule, failures, reason),
                        "detail": reason,
                    })
                    set_alert_rule_state(db, alert_rule["id"], check_id, True)
            elif alert_rule["alert_active"]:
                set_alert_rule_state(db, alert_rule["id"], check_id, False)

        db.execute("UPDATE checks SET alert_active = ? WHERE id = ?", (int(any_active), check_id))
        db.commit()

        for pending in pending_alerts:
            create_alert(
                db, check, "failure", pending["message"], pending["rule"]["id"], pending["detail"],
                context={
                    "rule_name": pending["rule"]["name"],
                    "failures": pending["failures"],
                    "window_minutes": pending["rule"]["alert_window_minutes"],
                    "threshold": pending["rule"]["alert_failures"],
                },
            )
            db.commit()
    finally:
        db.close()


def prune_old_results(db):
    cutoff = iso(now_utc() - timedelta(days=7))
    db.execute("DELETE FROM check_results WHERE checked_at < ?", (cutoff,))
    db.commit()


def run_pending_checks():
    db = open_db()
    current_time = now_utc()
    checks = db.execute(
        """
        SELECT id, frequency_minutes FROM checks
        WHERE next_run_at IS NULL OR next_run_at <= ?
        ORDER BY COALESCE(next_run_at, created_at)
        """,
        (iso(current_time),),
    ).fetchall()
    for check in checks:
        # Claim work before dispatch so overlapping scheduler sweeps don't run it twice.
        next_run_at = current_time + timedelta(minutes=check["frequency_minutes"])
        db.execute("UPDATE checks SET next_run_at = ? WHERE id = ?", (iso(next_run_at), check["id"]))
    db.commit()
    db.close()

    if checks:
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = {pool.submit(run_check, check["id"]): check["id"] for check in checks}
            for future in as_completed(futures):
                check_id = futures[future]
                try:
                    future.result()
                except Exception:
                    app.logger.exception("Unexpected error while running check %s", check_id)


def run_prune():
    db = open_db()
    prune_old_results(db)
    db.close()


def start_scheduler():
    if not SCHEDULER.running:
        SCHEDULER.add_job(
            run_pending_checks,
            "interval",
            seconds=30,
            id="pending-checks",
            replace_existing=True,
            max_instances=2,
            coalesce=True,
        )
        SCHEDULER.add_job(
            run_prune,
            "interval",
            hours=1,
            id="prune-results",
            replace_existing=True,
        )
        SCHEDULER.start()


def dashboard_data():
    db = get_db()
    window_start = iso(now_utc() - timedelta(hours=1))
    checks = db.execute(
        """
        SELECT c.*, COALESCE(rule_counts.alert_rule_count, 0) AS alert_rule_count,
               COALESCE(stats.success_count, 0) AS success_count,
               COALESCE(stats.failure_count, 0) AS failure_count
        FROM checks c
        LEFT JOIN (
            SELECT check_id, COUNT(*) AS alert_rule_count
            FROM alert_rule_checks
            GROUP BY check_id
        ) rule_counts ON rule_counts.check_id = c.id
        LEFT JOIN (
            SELECT check_id,
                   SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS success_count,
                   SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failure_count
            FROM check_results
            WHERE checked_at >= ?
            GROUP BY check_id
        ) stats ON stats.check_id = c.id
        ORDER BY c.name COLLATE NOCASE ASC
        """,
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


def row_to_dict(row):
    return dict(row) if row else None


@app.route("/api/dashboard")
def api_dashboard():
    checks, alerts = dashboard_data()
    return jsonify({
        "checks": [dict(r) for r in checks],
        "alerts": [dict(r) for r in alerts],
        "expected_status": EXPECTED_STATUS,
    })


@app.route("/api/checks", methods=["POST"])
def api_create_check():
    try:
        data = validate_check_json(get_json_object(request))
        timestamp = iso(now_utc())
        db = get_db()
        db.execute(
            """
            INSERT INTO checks (name, url, frequency_minutes, timeout_seconds, blackout_periods, created_at, next_run_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (data["name"], data["url"], data["frequency_minutes"], data["timeout_seconds"], data["blackout_periods"], timestamp, timestamp),
        )
        db.commit()
        return jsonify({"ok": True}), 201
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/checks/<int:check_id>")
def api_get_check(check_id):
    check = fetch_check(check_id)
    if check is None:
        return jsonify({"error": "Check not found."}), 404
    return jsonify(dict(check))


@app.route("/api/checks/<int:check_id>", methods=["PUT"])
def api_update_check(check_id):
    if fetch_check(check_id) is None:
        return jsonify({"error": "Check not found."}), 404
    try:
        data = validate_check_json(get_json_object(request))
        db = get_db()
        db.execute(
            """
            UPDATE checks
            SET name = ?, url = ?, frequency_minutes = ?, timeout_seconds = ?, blackout_periods = ?
            WHERE id = ?
            """,
            (data["name"], data["url"], data["frequency_minutes"], data["timeout_seconds"], data["blackout_periods"], check_id),
        )
        db.commit()
        return jsonify({"ok": True})
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400


@app.route("/api/checks/<int:check_id>", methods=["DELETE"])
def api_delete_check(check_id):
    db = get_db()
    check = db.execute("SELECT id FROM checks WHERE id = ?", (check_id,)).fetchone()
    if check is None:
        return jsonify({"error": "Check not found."}), 404
    db.execute("DELETE FROM alert_rule_states WHERE check_id = ?", (check_id,))
    db.execute("DELETE FROM alert_rule_checks WHERE check_id = ?", (check_id,))
    db.execute("DELETE FROM alerts WHERE check_id = ?", (check_id,))
    db.execute("DELETE FROM check_results WHERE check_id = ?", (check_id,))
    db.execute("DELETE FROM checks WHERE id = ?", (check_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/checks/<int:check_id>/run", methods=["POST"])
def api_trigger_check(check_id):
    if fetch_check(check_id) is None:
        return jsonify({"error": "Check not found."}), 404
    run_check(check_id)
    return jsonify({"ok": True})
