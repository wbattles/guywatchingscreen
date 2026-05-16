from datetime import timedelta

from flask import flash, redirect, render_template, request, url_for

from common import app, get_db, iso, now_local, parse_int_field
from communication import fetch_email_recipients, send_email_alert


def validate_alert_form(form):
    name = form.get("name", "").strip()
    alert_failures = parse_int_field(form, "alert_failures", 3, "Alert failures")
    alert_window_minutes = parse_int_field(form, "alert_window_minutes", 15, "Alert window")
    check_ids = sorted({int(value) for value in form.getlist("check_ids") if value.isdigit()})
    recipient_ids = sorted({int(value) for value in form.getlist("recipient_ids") if value.isdigit()})

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
        "recipient_ids": recipient_ids,
    }


def create_alert(db, check, alert_type, message, alert_rule_id=None, detail=None):
    delivered_via = "dashboard"
    try:
        delivered_via = send_email_alert(db, check, message, alert_rule_id)
    except Exception as exc:
        message = f"{message} (email send failed: {exc})"

    db.execute(
        """
        INSERT INTO alerts (check_id, alert_rule_id, created_at, alert_type, message, detail, delivered_via)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (check["id"], alert_rule_id, iso(now_local()), alert_type, message, detail, delivered_via),
    )


def failure_count_within_window(db, check_id, window_minutes):
    window_start = now_local() - timedelta(minutes=window_minutes)
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
        SELECT ar.*, COALESCE(ars.alert_active, 0) AS alert_active
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


def alert_settings_data():
    db = get_db()
    return db.execute(
        """
        SELECT ar.id,
               ar.name,
               ar.alert_failures,
               ar.alert_window_minutes,
               COUNT(DISTINCT arc.check_id) AS monitor_count,
               GROUP_CONCAT(DISTINCT c.name) AS monitor_names,
               COUNT(DISTINCT arer.recipient_id) AS communication_count,
               GROUP_CONCAT(DISTINCT r.email) AS communication_names
        FROM alert_rules ar
        LEFT JOIN alert_rule_checks arc ON arc.alert_rule_id = ar.id
        LEFT JOIN checks c ON c.id = arc.check_id
        LEFT JOIN alert_rule_email_recipients arer ON arer.alert_rule_id = ar.id
        LEFT JOIN communication_email_recipients r ON r.id = arer.recipient_id
        GROUP BY ar.id, ar.name, ar.alert_failures, ar.alert_window_minutes
        ORDER BY ar.name COLLATE NOCASE ASC
        """
    ).fetchall()


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


def alert_rule_recipient_ids(db, alert_rule_id):
    rows = db.execute(
        "SELECT recipient_id FROM alert_rule_email_recipients WHERE alert_rule_id = ?",
        (alert_rule_id,),
    ).fetchall()
    return [row["recipient_id"] for row in rows]


@app.route("/alerts")
def alerts_screen():
    return render_template("alerts.html", alert_settings=alert_settings_data())


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
    db.execute("UPDATE alert_rule_states SET alert_active = 0")
    db.execute("UPDATE checks SET alert_active = 0")
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
    checks = db.execute("SELECT id, name FROM checks ORDER BY name COLLATE NOCASE ASC").fetchall()
    recipients = fetch_email_recipients(db)
    selected_check_ids = []
    selected_recipient_ids = []
    if request.method == "POST":
        selected_check_ids = [int(value) for value in request.form.getlist("check_ids") if value.isdigit()]
        selected_recipient_ids = [int(value) for value in request.form.getlist("recipient_ids") if value.isdigit()]
        try:
            data = validate_alert_form(request.form)
            db.execute(
                """
                INSERT INTO alert_rules (name, alert_failures, alert_window_minutes, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (data["name"], data["alert_failures"], data["alert_window_minutes"], iso(now_local())),
            )
            alert_rule_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            for check_id in data["check_ids"]:
                db.execute("INSERT INTO alert_rule_checks (alert_rule_id, check_id) VALUES (?, ?)", (alert_rule_id, check_id))
            for recipient_id in data["recipient_ids"]:
                db.execute(
                    "INSERT INTO alert_rule_email_recipients (alert_rule_id, recipient_id) VALUES (?, ?)",
                    (alert_rule_id, recipient_id),
                )
            db.commit()
            flash("Alert created.", "success")
            return redirect(url_for("alerts_screen"))
        except ValueError as exc:
            flash(str(exc), "error")
    return render_template(
        "alert_form.html",
        checks=checks,
        recipients=recipients,
        alert_rule=None,
        selected_check_ids=selected_check_ids,
        selected_recipient_ids=selected_recipient_ids,
    )


@app.route("/alerts/<int:alert_rule_id>/edit", methods=["GET", "POST"])
def edit_alert_rule(alert_rule_id):
    db = get_db()
    checks = db.execute("SELECT id, name FROM checks ORDER BY name COLLATE NOCASE ASC").fetchall()
    recipients = fetch_email_recipients(db)
    alert_rule = fetch_alert_rule(alert_rule_id)
    if alert_rule is None:
        flash("Alert not found.", "error")
        return redirect(url_for("alerts_screen"))

    if request.method == "POST":
        selected_check_ids = [int(value) for value in request.form.getlist("check_ids") if value.isdigit()]
        selected_recipient_ids = [int(value) for value in request.form.getlist("recipient_ids") if value.isdigit()]
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
                db.execute("INSERT INTO alert_rule_checks (alert_rule_id, check_id) VALUES (?, ?)", (alert_rule_id, check_id))
            db.execute("DELETE FROM alert_rule_email_recipients WHERE alert_rule_id = ?", (alert_rule_id,))
            for recipient_id in data["recipient_ids"]:
                db.execute(
                    "INSERT INTO alert_rule_email_recipients (alert_rule_id, recipient_id) VALUES (?, ?)",
                    (alert_rule_id, recipient_id),
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
                recipients=recipients,
                alert_rule=alert_rule,
                selected_check_ids=selected_check_ids,
                selected_recipient_ids=selected_recipient_ids,
            )

    return render_template(
        "alert_form.html",
        checks=checks,
        recipients=recipients,
        alert_rule=alert_rule,
        selected_check_ids=alert_rule_check_ids(db, alert_rule_id),
        selected_recipient_ids=alert_rule_recipient_ids(db, alert_rule_id),
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
    db.execute("DELETE FROM alert_rule_email_recipients WHERE alert_rule_id = ?", (alert_rule_id,))
    db.execute("DELETE FROM alerts WHERE alert_rule_id = ?", (alert_rule_id,))
    db.execute("DELETE FROM alert_rules WHERE id = ?", (alert_rule_id,))
    db.commit()
    flash("Alert deleted.", "success")
    return redirect(url_for("alerts_screen"))
