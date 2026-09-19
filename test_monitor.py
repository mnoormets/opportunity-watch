import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
import monitor as m


TARGET = {"id": "test", "name": "Test", "url": "https://example.org/apply", "selector": "#application"}


def html(text):
    return f'<html><div id="application">{text}</div></html>'


def state():
    return {"version": 1, "targets": {}, "outbox": [], "hosts": {}, "delivery_after": {}}


class MonitorTests(unittest.TestCase):
    def test_closed_to_open_and_no_duplicate(self):
        data, fetcher = state(), Mock()
        fetcher.page.return_value = html("Applications Closed")
        m.check(TARGET, fetcher, data, {"discord": "url"})
        fetcher.page.return_value = html("Applications are open. Apply Now")
        m.check(TARGET, fetcher, data, {"discord": "url"})
        self.assertIn("CLOSED -> OPEN", data["outbox"][-1]["text"])
        m.check(TARGET, fetcher, data, {"discord": "url"})
        self.assertEqual(len(data["outbox"]), 2)

    def test_waitlist_to_open(self):
        self.assertEqual(m.extract(html("Sign up for updates"), TARGET)["status"], "CLOSED")
        self.assertEqual(m.extract(html("Open Cohort"), TARGET)["status"], "OPEN")

    def test_mixed_and_unknown(self):
        self.assertEqual(m.extract(html("Applications closed. Apply now"), TARGET)["status"], "MIXED")
        self.assertEqual(m.extract(html("Welcome"), TARGET)["status"], "UNKNOWN")
        self.assertEqual(m.extract(html("Applications are not yet open"), TARGET)["status"], "CLOSED")

    def test_script_and_whitespace_noise(self):
        a = m.extract(html("Apply   Now<script>123</script>"), TARGET)
        b = m.extract(html("Apply Now<script>456</script>"), TARGET)
        self.assertEqual(a, b)

    def test_link_destination_change(self):
        a = m.extract(html('<a href="/one">Apply</a>'), TARGET)
        b = m.extract(html('<a href="/two">Apply</a>'), TARGET)
        self.assertNotEqual(a["hash"], b["hash"])

    def test_missing_selector_fails(self):
        with self.assertRaises(m.FetchError):
            m.extract("<body>Apply Now</body>", TARGET)

    def test_failure_preserves_snapshot_and_recovery(self):
        data, fetcher = state(), Mock()
        fetcher.page.return_value = html("Applications Closed")
        m.check(TARGET, fetcher, data, {"discord": "url"})
        snapshot = data["targets"]["test"]["snapshot"].copy()
        fetcher.page.side_effect = requests.Timeout()
        for _ in range(3):
            data["targets"]["test"]["next_check"] = 0
            m.check(TARGET, fetcher, data, {"discord": "url"})
        self.assertEqual(data["targets"]["test"]["snapshot"], snapshot)
        self.assertIn("MONITOR ERROR", data["outbox"][-1]["text"])
        data["targets"]["test"]["next_check"] = 0
        fetcher.page.side_effect = None
        m.check(TARGET, fetcher, data, {"discord": "url"})
        self.assertIn("MONITOR RECOVERED", data["outbox"][-1]["text"])

    def test_partial_delivery_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path, data = Path(directory) / "state.json", state()
            destinations = {"discord": "url", "telegram": ("token", "id")}
            m.enqueue(data, destinations, "event")
            with patch.object(m, "deliver", side_effect=[None, requests.Timeout()]):
                self.assertTrue(m.flush(Mock(), data, destinations, path))
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["outbox"][0]["pending"], ["telegram"])
            loaded["delivery_after"] = {}
            with patch.object(m, "deliver") as send:
                self.assertFalse(m.flush(Mock(), loaded, destinations, path))
                self.assertEqual(send.call_args.args[1], "telegram")
                self.assertEqual(send.call_count, 1)

    def test_retry_after_formats(self):
        self.assertEqual(m.retry_seconds("120"), 120)
        self.assertEqual(m.retry_seconds("garbage"), 0)
        with patch.object(m.time, "time", return_value=0):
            self.assertEqual(m.retry_seconds("Thu, 01 Jan 1970 00:02:00 GMT"), 120)

    def test_robots_disallow(self):
        fetcher = m.Fetcher(Mock(), state())
        fetcher.get = Mock(return_value=(b"User-agent: *\nDisallow: /apply", "utf-8", "text/plain"))
        with self.assertRaises(m.FetchError):
            fetcher.page(TARGET["url"])
        self.assertEqual(fetcher.get.call_count, 1)

    def test_429_sets_host_cooldown(self):
        session = Mock()
        from unittest.mock import MagicMock
        session.get.return_value = MagicMock()
        response = session.get.return_value.__enter__.return_value
        response.status_code = 429
        response.headers = {"Retry-After": "1800"}
        data = state()
        fetcher = m.Fetcher(session, data)
        with self.assertRaises(m.FetchError):
            fetcher.get(TARGET["url"])
        self.assertGreater(data["hosts"]["https://example.org"], m.time.time() + 1700)
        with self.assertRaises(m.FetchError):
            fetcher.get(TARGET["url"])
        self.assertEqual(session.get.call_count, 1)

    def test_html_attribute_changes(self):
        target = {**TARGET, "mode": "html"}
        self.assertNotEqual(m.extract(html('<button disabled>Apply</button>'), target)["hash"],
                            m.extract(html('<button>Apply</button>'), target)["hash"])


if __name__ == "__main__":
    unittest.main()
