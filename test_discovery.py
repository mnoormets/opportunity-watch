import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import discovery as d


NOW = 1789845600
SOURCE = {"id": "sample", "name": "Test source", "kind": "remoteok", "interval_seconds": 3600}


def sample(**fields):
    row = d.item(SOURCE, "Junior AI trainer", "https://example.org/job", location="Worldwide", date=NOW - 3600, remote=True)
    row.update(fields)
    return row


class DiscoveryTests(unittest.TestCase):
    def test_worldwide_and_europe(self):
        self.assertIsNotNone(d.rank(sample(), NOW))
        self.assertIsNotNone(d.rank(sample(location="Europe"), NOW))

    def test_us_only_is_excluded(self):
        self.assertIsNone(d.rank(sample(location="USA"), NOW))
        self.assertIsNone(d.rank(sample(location="", description="Must reside in the United States"), NOW))

    def test_stale_and_unpaid_excluded(self):
        self.assertIsNone(d.rank(sample(published=NOW - 30 * 86400), NOW))
        self.assertIsNone(d.rank(sample(description="This is an unpaid internship."), NOW))

    def test_generic_ai_mention_not_a_match(self):
        self.assertIsNone(d.rank(sample(title="Accountant", description="Our company uses AI"), NOW))

    def test_awarded_grant_not_an_open_opportunity(self):
        self.assertIsNone(d.rank(sample(kind="news", title="University awarded AI research grant"), NOW))

    def test_free_course_excluded_paid_call_included(self):
        self.assertIsNone(d.rank(sample(kind="news", title="Reface launches free online internship for AI designers"), NOW))
        self.assertIsNotNone(d.rank(sample(kind="news", title="Applications open for paid AI internship"), NOW))

    def test_unknown_location_is_explicit(self):
        rating = d.rank(sample(location=""), NOW)
        self.assertIn("kinnitamata", rating[1])

    def test_tracking_deduplicates(self):
        self.assertEqual(d.canonical("https://EXAMPLE.org/job/?utm_source=a#top"), "https://example.org/job")
        a = sample(company="Example Ltd", url="https://first.org/job")
        b = sample(company="Example Ltd", url="https://second.org/job")
        self.assertEqual(d.fingerprint(a), d.fingerprint(b))

    def test_bad_feed_is_not_silently_empty(self):
        with self.assertRaises(ValueError):
            d.parse({**SOURCE, "kind": "news"}, b"<html>blocked</html>")

    def test_xml_entities_rejected(self):
        with self.assertRaises(ValueError):
            d.parse({**SOURCE, "kind": "news"}, b'<!DOCTYPE rss><rss/>')

    def test_restart_does_not_alert_twice(self):
        config = {"sources": [SOURCE], "max_age_days": 14, "alerts_per_run": 5, "initial_alerts": 4, "alerts_per_day": 20}
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            report = Path(directory) / "report.json"
            with patch.object(d.m, "channels", return_value={"discord": "placeholder"}), patch.object(d, "fetch", return_value=[sample()]), patch.object(d.time, "sleep"), patch.object(d.time, "time", return_value=NOW), patch.object(d.m, "deliver") as send:
                self.assertEqual(d.run(config, state, report), 0)
                self.assertEqual(send.call_count, 1)
                saved = json.loads(state.read_text(encoding="utf-8"))
                saved["sources"]["sample"]["next_check"] = 0
                d.m.save(state, saved)
                self.assertEqual(d.run(config, state, report), 0)
                self.assertEqual(send.call_count, 1)


if __name__ == "__main__":
    unittest.main()
