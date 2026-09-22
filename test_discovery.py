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
    def test_ordinary_jobs_and_internships(self):
        for title in ['Junior Software Developer', 'IT Support Specialist', 'Shopify Developer', 'Software Engineering Internship']:
            self.assertIsNotNone(d.rank(sample(title=title, location='Estonia'), NOW))
        self.assertEqual(d.category(sample(title='Software Engineering Internship')), 'programme')
        self.assertIsNone(d.rank(sample(title='Senior Software Engineer'), NOW))
        self.assertIsNone(d.rank(sample(title='Software Engineer', description='Requires 5 years of experience'), NOW))

    def test_curated_relocation_is_conditional(self):
        row=sample(title='Software Engineering Intern', location='San Francisco, USA', remote=False, relocation_employer=True)
        self.assertIn('Kolimine pärast pakkumist', d.rank(row, NOW)[1])
        self.assertIsNone(d.rank(dict(row, relocation_employer=False), NOW))
        self.assertIsNone(d.rank(dict(row, description='We cannot sponsor visas.'), NOW))
        self.assertIsNone(d.rank(dict(row, location='India'), NOW))
        self.assertIsNone(d.rank(dict(row, title='Research Engineer'), NOW))
        self.assertIsNone(d.rank(sample(title='AI Trainer - Swedish',location='Estonia'), NOW))
        self.assertIsNone(d.rank(dict(row, description='US citizen required'), NOW))

    def test_official_parsers(self):
        lever=[{'text':'Junior Developer','hostedUrl':'https://jobs.lever.co/example/1','categories':{'location':'Tallinn'},'descriptionPlain':'Build apps','lists':[{'content':'<li>PostgreSQL</li>'}]}]
        row=d.parse(dict(SOURCE,kind='lever',company='Example',relocation_employer=True),json.dumps(lever).encode())[0]
        self.assertTrue(row['relocation_employer'])
        self.assertIn('PostgreSQL',row['description'])
        gh={'jobs':[{'title':'IT Support','absolute_url':'https://example.org/1','location':{'name':'Estonia'},'content':'&lt;p&gt;Help users&lt;/p&gt;'}]}
        self.assertEqual(d.parse(dict(SOURCE,kind='greenhouse'),json.dumps(gh).encode())[0]['description'],'Help users')
        ashby={'jobs':[{'title':'Developer','jobUrl':'https://example.org/1','location':'Europe','isRemote':True},{'isListed':False}]}
        self.assertEqual(len(d.parse(dict(SOURCE,kind='ashby'),json.dumps(ashby).encode())),1)

    def test_estonia_only_geography(self):
        for location, remote in [('Berlin, Germany', False), ('Berlin, Germany', True),
                                 ('France', True), ('Remote', True), ('Europe', False)]:
            with self.subTest(location=location, remote=remote):
                self.assertIsNone(d.rank(sample(location=location, remote=remote), NOW))
        for location, remote in [('Tallinn, Estonia', False), ('Estonia', True),
                                 ('Worldwide', True), ('Europe', True), ('EU', True)]:
            with self.subTest(location=location, remote=remote):
                self.assertIsNotNone(d.rank(sample(location=location, remote=remote), NOW))

    def test_mandatory_residence_overrides_feed(self):
        for clause in ['Must be currently located in Ukraine.', 'Must reside in Germany.',
                       'You must be authorized to work in the United States.', 'Remote US-only.']:
            with self.subTest(clause=clause):
                self.assertIsNone(d.rank(sample(description=clause), NOW))
        self.assertIsNotNone(d.rank(sample(description='Must be located in Estonia.'), NOW))

    def test_missing_location_needs_explicit_remote_scope(self):
        self.assertIsNone(d.rank(sample(location='', description='We are a global international company. Remote job.'), NOW))
        self.assertIsNotNone(d.rank(sample(location='', description='AI engineer | Remote | Europe'), NOW))

    def test_old_queue_removed_before_flush_archive_preserved(self):
        state={'candidates':{'old':sample(), 'new':sample(geo_policy=d.GEO_POLICY)},
               'opportunities':{'old':sample(), 'new':sample(geo_policy=d.GEO_POLICY)},
               'outbox':[{'opportunity_id':'old'},{'opportunity_id':'new'},{'message':'Source error'}]}
        d.prune_geography(state)
        self.assertEqual(list(state['candidates']), ['new'])
        self.assertEqual(len(state['outbox']), 2)
        self.assertEqual(len(state['opportunities']), 2)

    def test_primary_priority_and_secondary_cap(self):
        rows = {str(i): sample(title="AI engineer", score=4, published=NOW) for i in range(10)}
        rows["programme"] = sample(title="AI fellowship applications open", kind="news", score=1, published=NOW)
        for i in range(10):
            rows["side"+str(i)] = sample(title="AI hackathon prize", kind="news", score=99, published=NOW)
        selected = d.choose(rows, 5, 1)
        self.assertEqual(len(selected), 5)
        self.assertEqual(selected[0][0], "programme")
        self.assertEqual(sum(d.category(r)=="secondary" for _,r in selected), 1)
        self.assertTrue(all(d.category(r)!="secondary" for _,r in d.choose(rows, 5, 0)))

    def test_international_is_not_an_internship(self):
        for title in ('Marketing Events Manager, International', 'Applied AI Engineer, Government, International', 'International Marketing Lead, SMB Ads'):
            self.assertFalse(d.EARLY.search(title))
            self.assertFalse(d.BEGINNER.search(title))
            self.assertIsNone(d.rank(sample(title=title, location='London, UK', relocation_employer=True), NOW))
        self.assertTrue(d.EARLY.search('IT Support Technician Intern'))
        self.assertTrue(d.BEGINNER.search('Software Engineering Internship'))

    def test_social_discussion_is_not_a_vacancy(self):
        self.assertIsNone(d.rank(sample(kind="news", discovery_only=True, title="Which AI video model for your next paid project?"), NOW))
        self.assertIsNotNone(d.rank(sample(kind="news", discovery_only=True, title="Applications open for paid AI fellowship"), NOW))

    def test_social_publisher_is_checked(self):
        source = dict(SOURCE, kind="news", publisher_domain="youtube.com")
        xml = b'<rss><channel><item><title>Paid AI internship</title><link>https://example.org/1</link><source url="https://unrelated.org">Other</source></item></channel></rss>'
        self.assertEqual(d.parse(source, xml), [])
        self.assertEqual(len(d.parse(source, xml.replace(b'https://unrelated.org',b'https://www.youtube.com'))), 1)

    def test_hn_excludes_job_seekers_and_general_discussion(self):
        source = dict(SOURCE, kind="hn")
        hits = [{"objectID":"123", "parent_id":10, "story_id":10, "story_title":"Ask HN: Who is hiring?", "comment_text":"AI engineer | Remote | Europe", "created_at":NOW},
                {"objectID":"124", "parent_id":10, "story_id":10, "story_title":"Ask HN: Who is hiring?", "comment_text":"SEEKING WORK as AI engineer"},
                {"objectID":"125", "parent_id":10, "story_id":10, "story_title":"Discussion about AI", "comment_text":"AI engineer | Remote"}]
        rows = d.parse(source, json.dumps({"hits":hits}).encode())
        self.assertEqual(len(rows), 1)
        self.assertEqual(d.parse(source, json.dumps({"hits":[dict(hits[0], parent_id=123)]}).encode()), [])
        self.assertEqual(rows[0]["url"], "https://news.ycombinator.com/item?id=123")
        self.assertIsNotNone(d.rank(rows[0], NOW))

    def test_jobicy_salary_units_retained(self):
        source = dict(SOURCE, kind="jobicy")
        row = {"jobTitle":"AI Engineer", "url":"https://jobicy.com/jobs/1", "jobGeo":"Europe", "salaryMin":50000, "salaryCurrency":"EUR", "salaryPeriod":"yearly"}
        parsed = d.parse(source, json.dumps({"jobs":[row]}).encode())[0]
        self.assertIn("EUR / yearly", parsed["salary"])
        self.assertIsNotNone(d.rank(parsed, NOW))

    def test_crime_bounties_excluded(self):
        self.assertIsNone(d.rank(sample(kind="news", title="Pakistan announces bounty on wanted suspect"), NOW))
        self.assertIsNotNone(d.rank(sample(kind="news", title="Applications open for software bug bounty program"), NOW))
        self.assertTrue(d.unrelated_bounty({"title": "Pakistan announces bounty on wanted suspect"}))

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
        self.assertIsNone(d.rank(sample(kind="news", title="How to use AI in residency applications? Learn the rules"), NOW))
        self.assertIsNone(d.rank(sample(kind="news", title="Someone joins Open Source AI Fellowship"), NOW))
        self.assertIsNone(d.rank(sample(kind="news", title="University awarded AI research grant"), NOW))

    def test_free_course_excluded_paid_call_included(self):
        self.assertIsNone(d.rank(sample(kind="news", title="Reface launches free online internship for AI designers"), NOW))
        self.assertIsNotNone(d.rank(sample(kind="news", title="Applications open for paid AI internship"), NOW))

    def test_unknown_location_is_explicit(self):
        rating = d.rank(sample(location=""), NOW)
        self.assertIsNone(rating)

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
