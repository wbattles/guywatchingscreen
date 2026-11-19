import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("FLASK_DEBUG", "1")
os.environ.setdefault("RUN_SCHEDULER", "0")

from common import looks_like_email, parse_blackout_periods, is_in_blackout, iso, now_utc
from datetime import time


class TestLooksLikeEmail(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(looks_like_email("user@example.com"))
        self.assertTrue(looks_like_email("a@b.co"))

    def test_invalid(self):
        self.assertFalse(looks_like_email("notanemail"))
        self.assertFalse(looks_like_email("@.x"))
        self.assertFalse(looks_like_email("x@y."))
        self.assertFalse(looks_like_email(""))
        self.assertFalse(looks_like_email(None))


class TestParseBlackoutPeriods(unittest.TestCase):
    def test_valid(self):
        result = parse_blackout_periods("23:00-06:00\n12:30-13:00")
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], (time(23, 0), time(6, 0)))
        self.assertEqual(result[1], (time(12, 30), time(13, 0)))

    def test_empty(self):
        self.assertEqual(parse_blackout_periods(""), [])
        self.assertEqual(parse_blackout_periods(None), [])

    def test_invalid(self):
        with self.assertRaises(ValueError):
            parse_blackout_periods("notaperiod")


class TestIsInBlackout(unittest.TestCase):
    def test_in_window(self):
        self.assertTrue(is_in_blackout("10:00-12:00", current_time=time(11, 0)))

    def test_outside_window(self):
        self.assertFalse(is_in_blackout("10:00-12:00", current_time=time(13, 0)))

    def test_overnight_window(self):
        self.assertTrue(is_in_blackout("23:00-06:00", current_time=time(1, 0)))
        self.assertFalse(is_in_blackout("23:00-06:00", current_time=time(12, 0)))

    def test_empty(self):
        self.assertFalse(is_in_blackout("", current_time=time(11, 0)))


class TestIso(unittest.TestCase):
    def test_naive_passthrough(self):
        from datetime import datetime
        dt = datetime(2026, 1, 1, 12, 0, 0)
        self.assertEqual(iso(dt), "2026-01-01T12:00:00")

    def test_aware_stripped(self):
        result = iso(now_utc())
        self.assertNotIn("+", result)
        self.assertNotIn("Z", result)


class TestAppImport(unittest.TestCase):
    def test_app_imports(self):
        import app

        self.assertIsNotNone(app.app)

    def test_health_route(self):
        import app

        client = app.app.test_client()
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), "OK")


class TestApiValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app

        cls.client = app.app.test_client()

    def test_create_check_rejects_non_object_json(self):
        response = self.client.post("/api/checks", json=[])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "Request body must be a JSON object.")

    def test_create_alert_rule_rejects_invalid_json(self):
        response = self.client.post(
            "/api/alert-rules",
            data="{",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "Request body must be a JSON object.")

    def test_create_email_rejects_non_object_json(self):
        response = self.client.post("/api/communication/emails", json=None)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "Request body must be a JSON object.")

    def test_delete_missing_alert_returns_404(self):
        response = self.client.delete("/api/alerts/999999")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "Alert not found.")

    def test_delete_missing_email_returns_404(self):
        response = self.client.delete("/api/communication/emails/999999")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "Email not found.")


class TestSchedulerClaims(unittest.TestCase):
    def setUp(self):
        import app
        from common import get_db

        self.app = app.app
        self.ctx = self.app.app_context()
        self.ctx.push()
        db = get_db()
        db.execute("DELETE FROM alert_rule_states")
        db.execute("DELETE FROM alert_rule_checks")
        db.execute("DELETE FROM alert_rule_email_recipients")
        db.execute("DELETE FROM alerts")
        db.execute("DELETE FROM check_results")
        db.execute("DELETE FROM alert_rules")
        db.execute("DELETE FROM communication_email_recipients")
        db.execute("DELETE FROM checks")
        db.commit()

    def tearDown(self):
        self.ctx.pop()

    def test_run_pending_checks_claims_due_work(self):
        from common import get_db
        import websites

        db = get_db()
        db.execute(
            """
            INSERT INTO checks (name, url, frequency_minutes, timeout_seconds, blackout_periods, created_at, next_run_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("Example", "https://example.com", 5, 10, "", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        db.commit()

        with patch("websites.run_check") as mock_run:
            websites.run_pending_checks()
            websites.run_pending_checks()

        self.assertEqual(mock_run.call_count, 1)


class TestAlertEmailSanitization(unittest.TestCase):
    def setUp(self):
        import app
        from common import get_db

        self.app = app.app
        self.ctx = self.app.app_context()
        self.ctx.push()
        db = get_db()
        db.execute("DELETE FROM alert_rule_states")
        db.execute("DELETE FROM alert_rule_checks")
        db.execute("DELETE FROM alert_rule_email_recipients")
        db.execute("DELETE FROM alerts")
        db.execute("DELETE FROM check_results")
        db.execute("DELETE FROM alert_rules")
        db.execute("DELETE FROM communication_email_recipients")
        db.execute("DELETE FROM checks")
        db.commit()

    def tearDown(self):
        self.ctx.pop()

    def test_create_alert_does_not_store_smtp_exception_text(self):
        from common import get_db
        import alerts

        db = get_db()
        db.execute(
            """
            INSERT INTO checks (name, url, frequency_minutes, timeout_seconds, blackout_periods, created_at, next_run_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("Example", "https://example.com", 5, 10, "", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        check = db.execute("SELECT * FROM checks").fetchone()

        with self.assertLogs("common", level="ERROR") as logs:
            with patch("alerts.send_email_alert", side_effect=RuntimeError("SMTP auth failed for secret-password")):
                alerts.create_alert(db, check, "failure", "Base message")

        alert = db.execute("SELECT message FROM alerts").fetchone()
        self.assertEqual(alert["message"], "Base message (email delivery failed)")
        self.assertNotIn("secret-password", alert["message"])
        self.assertIn("Failed to send alert email for check", logs.output[0])
        self.assertNotIn("secret-password", logs.output[0])


    def _insert_check(self, db):
        db.execute(
            """
            INSERT INTO checks (name, url, frequency_minutes, timeout_seconds, blackout_periods, created_at, next_run_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("Example", "https://example.com", 5, 10, "", "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        return db.execute("SELECT * FROM checks").fetchone()

    def test_create_alert_posts_discord_webhook(self):
        from common import get_db
        import alerts

        db = get_db()
        check = self._insert_check(db)
        hook = "https://discord.com/api/webhooks/1/token"
        with patch.dict(os.environ, {"WEBHOOK_URL": hook}), patch("communication.requests.post") as post:
            alerts.create_alert(
                db, check, "failure", "Example is down", alert_rule_id=7,
                detail="Got HTTP 500 Internal Server Error, expected 200",
                context={"rule_name": "prod", "failures": 2, "window_minutes": 15, "threshold": 1},
            )

        post.assert_called_once()
        args, kwargs = post.call_args
        self.assertEqual(args[0], hook)
        payload = kwargs["json"]
        self.assertEqual(payload["allowed_mentions"], {"parse": []})
        embed = payload["embeds"][0]
        self.assertIn("Example is down", embed["title"])
        self.assertEqual(embed["description"], "Got HTTP 500 Internal Server Error, expected 200")
        self.assertEqual(embed["url"], "https://example.com")
        fields = {f["name"]: f["value"] for f in embed["fields"]}
        self.assertEqual(fields["Alert rule"], "prod")
        self.assertEqual(fields["Failures"], "2 failures in 15 min")
        self.assertTrue(embed["timestamp"].endswith("Z"))
        alert = db.execute("SELECT message, delivered_via FROM alerts").fetchone()
        self.assertEqual(alert["delivered_via"], "webhook")
        self.assertEqual(alert["message"], "Example is down")

    def test_create_alert_webhook_failure_hides_url(self):
        from common import get_db
        import alerts

        db = get_db()
        check = self._insert_check(db)
        hook = "https://discord.com/api/webhooks/1/super-secret-token"
        with self.assertLogs("common", level="ERROR") as logs:
            with patch.dict(os.environ, {"WEBHOOK_URL": hook}), \
                 patch("communication.requests.post", side_effect=RuntimeError(f"failed {hook}")):
                alerts.create_alert(db, check, "failure", "Base message", alert_rule_id=7)

        alert = db.execute("SELECT message, delivered_via FROM alerts").fetchone()
        self.assertEqual(alert["message"], "Base message (webhook delivery failed)")
        self.assertEqual(alert["delivered_via"], "dashboard")
        self.assertNotIn("super-secret-token", logs.output[0])

    def test_webhook_skipped_without_rule_or_url(self):
        from common import get_db
        import alerts

        db = get_db()
        check = self._insert_check(db)
        with patch.dict(os.environ, {"WEBHOOK_URL": "https://ntfy.sh/topic"}), patch("communication.requests.post") as post:
            alerts.create_alert(db, check, "failure", "No rule")
        post.assert_not_called()
        env = {k: v for k, v in os.environ.items() if k != "WEBHOOK_URL"}
        with patch.dict(os.environ, env, clear=True), patch("communication.requests.post") as post:
            alerts.create_alert(db, check, "failure", "No url", alert_rule_id=7)
        post.assert_not_called()

    def test_communication_api_does_not_leak_webhook_url(self):
        hook = "https://discord.com/api/webhooks/1/super-secret-token"
        with patch.dict(os.environ, {"WEBHOOK_URL": hook}):
            resp = self.app.test_client().get("/api/communication")
        self.assertEqual(resp.get_json()["webhook"], {"configured": True, "kind": "discord"})
        self.assertNotIn(b"super-secret-token", resp.data)


class TestWebhookKind(unittest.TestCase):
    def test_kinds(self):
        from communication import webhook_kind
        self.assertEqual(webhook_kind("https://discord.com/api/webhooks/1/x"), "discord")
        self.assertEqual(webhook_kind("https://canary.discord.com/api/webhooks/1/x"), "discord")
        self.assertEqual(webhook_kind("https://hooks.slack.com/services/x"), "slack")
        self.assertEqual(webhook_kind("https://ntfy.sh/topic"), "generic")
        self.assertEqual(webhook_kind("https://discord.com.evil.example/x"), "generic")


class TestFailureDescriptions(unittest.TestCase):
    def _response(self, code, location=None):
        from unittest.mock import Mock
        r = Mock()
        r.status_code = code
        r.headers = {"Location": location} if location else {}
        return r

    def test_status(self):
        from websites import describe_status
        self.assertEqual(describe_status(self._response(404)), "Got HTTP 404 Not Found, expected 200")
        self.assertEqual(describe_status(self._response(599)), "Got HTTP 599, expected 200")
        self.assertIn("Redirected to /docs (HTTP 307 Temporary Redirect)", describe_status(self._response(307, "/docs")))

    def test_exceptions(self):
        import requests
        from websites import describe_exception
        self.assertEqual(describe_exception(requests.exceptions.ReadTimeout("x"), 10), "No response within 10s (timed out)")
        self.assertIn("connection timed out", describe_exception(requests.exceptions.ConnectTimeout("x"), 5))
        dns = requests.exceptions.ConnectionError("HTTPSConnectionPool(host='nope.invalid'): NameResolutionError: Failed to resolve")
        self.assertEqual(describe_exception(dns, 10), "Couldn't find the host (DNS lookup failed)")
        refused = requests.exceptions.ConnectionError("[Errno 61] Connection refused")
        self.assertEqual(describe_exception(refused, 10), "Connection refused (nothing is listening there)")
        cert = requests.exceptions.SSLError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")
        self.assertEqual(describe_exception(cert, 10), "TLS certificate isn't valid (verification failed)")

    def test_alert_sentence(self):
        from websites import describe_alert
        rule = {"name": "prod", "alert_window_minutes": 15, "alert_failures": 1}
        self.assertEqual(
            describe_alert({"name": "API"}, rule, 1, "Got HTTP 404 Not Found, expected 200"),
            'API is down: Got HTTP 404 Not Found, expected 200. 1 failure in the last 15 min; alert rule "prod" fires at 1 failure.',
        )

if __name__ == "__main__":
    unittest.main()
