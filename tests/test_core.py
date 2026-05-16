import os
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
