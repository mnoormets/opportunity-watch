"""Discover previously unknown opportunities from job APIs and news-search RSS."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import html
import json
import logging
from pathlib import Path
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup
import requests
import monitor as m

AI = re.compile(r"\b(ai|artificial intelligence|machine learning|llm|generative|data annotat\w*|data label\w*|prompt engineer\w*|search evaluat\w*|internet assess\w*|tehisintellekt)\b", re.I)
OPPORTUNITY = re.compile(r"\b(fellowship\w*|residenc\w*|internship\w*|scholarship\w*|stipend\w*|hackathon\w*|bount\w*|grant\w*|accelerator\w*|paid research|paid user testing|paid participants|häkaton\w*|stipendium\w*|toetus\w*|taotlusvoor\w*)\b", re.I)
ACTION = re.compile(r"\b(apply|application\w*|open|launch\w*|hiring|recruit\w*|register|registration|prize\w*|announc\w*|kandideeri\w*|taotlusvoor\w*|auhinnafond\w*|tasustatud)\b", re.I)
NEGATIVE = re.compile(r"\b(how to|guide to|tips for|residency applications|medical residency|joins? .*fellowship|selected for|accepted into|graduates? from|layoffs?|scams?|fraud|applications? (?:are )?closed|deadline (?:has )?passed|winners? announced|awarded|receives? grant|lands? .*grant|unpaid|volunteer|pay to apply|get rich|passive income|crypto airdrop)\b", re.I)
EU = re.compile(r"\b(europe|european|eu|eea|emea|estonia|eesti|tallinn|tartu|germany|deutschland|berlin|munich|france|paris|netherlands|amsterdam|ireland|dublin|spain|portugal|poland|italy|sweden|finland|denmark|belgium|austria|czech|latvia|lithuania|romania|bulgaria|croatia|slovenia|slovakia|hungary|greece|cyprus|malta|luxembourg)\b", re.I)
WORLD = re.compile(r"\b(worldwide|global|anywhere|international|all countries|work from anywhere)\b", re.I)
OUTSIDE = re.compile(r"\b(united states|usa|us only|u\.s\.|canada|united kingdom|uk only|australia|new zealand|india|pakistan|philippines|singapore|brazil|latam|north america)\b", re.I)
BEGINNER = re.compile(r"\b(junior|entry.level|no experience|intern\w*|trainee|annotat\w*|rater|evaluator|trainer|fellowship\w*)\b", re.I)
PAY = re.compile(r"\b(paid|stipend\w*|funded|salary|prize\w*|bount\w*|grant\w*|scholarship\w*|stipendium\w*|auhinnafond\w*|tasustatud|toetus\w*)\b|[$€£]\s*\d", re.I)


def plain(value):
    return re.sub(r"\s+", " ", BeautifulSoup(str(value or ""), "html.parser").get_text(" ")).strip()


def canonical(url):
    p = urlsplit(html.unescape(url))
    if p.scheme not in ("https", "http") or not p.hostname or p.username or p.password:
        raise ValueError("Invalid item URL")
    query = [(k, v) for k, v in parse_qsl(p.query) if not k.startswith("utm_") and k not in ("fbclid", "gclid", "ref")]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urlencode(query), ""))


def timestamp(value):
    if isinstance(value, (int, float)):
        return float(value)
    if not value:
        return None
    try:
        date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            date = parsedate_to_datetime(str(value))
        except (ValueError, TypeError, OverflowError):
            return None
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return date.timestamp()


def item(source, title, url, description="", location="", company="", date=None, salary="", remote=False):
    return {"source": source["name"], "source_id": source["id"], "kind": source["kind"],
            "title": plain(title)[:220], "url": canonical(url), "description": plain(description)[:8000],
            "location": plain(location)[:180], "company": plain(company)[:120], "published": timestamp(date),
            "salary": plain(salary)[:180], "remote": bool(remote), "discovery_only": bool(source.get("discovery_only"))}


def parse(source, body):
    kind = source["kind"]
    if kind in ("news", "rss_jobs"):
        if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
            raise ValueError("Unsupported XML declarations")
        root = ET.fromstring(body)
        if root.tag != "rss" or root.find("channel") is None:
            raise ValueError("Expected RSS channel")
        rows = []
        for node in root.findall("./channel/item"):
            title, url = node.findtext("title"), node.findtext("link")
            if title and url:
                origin = node.find("source")
                required = source.get("publisher_domain")
                if required and (origin is None or urlsplit(origin.get("url", "")).hostname not in (required, "www." + required)):
                    continue
                rows.append(item(source, title, url, node.findtext("description"), date=node.findtext("pubDate"), remote=kind == "rss_jobs"))
        return rows
    data = json.loads(body)
    rows = []
    if kind == "hn":
        for hit in data["hits"]:
            text = plain(hit.get("comment_text"))
            if hit.get("parent_id") != hit.get("story_id") or hit.get("parent_id") is None:
                continue
            if not text or not re.search(r"who is hiring|freelancer", hit.get("story_title") or "", re.I):
                continue
            if re.search(r"SEEKING WORK|interested in (?:the|this)|looking for (?:a |my next )?(?:job|role)|available for hire", text[:180], re.I):
                continue
            rows.append(item(source, text[:220], "https://news.ycombinator.com/item?id=" + str(int(hit["objectID"])),
                             text, date=hit.get("created_at"), remote=bool(re.search(r"remote", text, re.I))))
    elif kind == "jobicy":
        for row in data["jobs"]:
            salary = ""
            if row.get("salaryMin") and row.get("salaryCurrency") and row.get("salaryPeriod"):
                salary = f"{row['salaryMin']}–{row.get('salaryMax') or row['salaryMin']} {row['salaryCurrency']} / {row['salaryPeriod']}"
            rows.append(item(source, row["jobTitle"], row["url"], row.get("jobDescription"), row.get("jobGeo"),
                             row.get("companyName"), row.get("pubDate"), salary=salary, remote=True))
    elif kind == "remoteok":
        if not isinstance(data, list):
            raise ValueError("Expected Remote OK list")
        for row in data:
            if not row.get("position"):
                continue
            # Salary units/currency are not guaranteed by this feed: do not invent them.
            rows.append(item(source, row["position"], row["url"], row.get("description"),
                             row.get("location"), row.get("company"), row.get("date"), remote=True))
    elif kind == "arbeitnow":
        for row in data["data"]:
            rows.append(item(source, row["title"], row["url"], row.get("description"), row.get("location"),
                             row.get("company_name"), row.get("created_at"), remote=row.get("remote")))
    elif kind == "remotive":
        for row in data["jobs"]:
            rows.append(item(source, row["title"], row["url"], row.get("description"), row.get("candidate_required_location"),
                             row.get("company_name"), row.get("publication_date"), row.get("salary"), remote=True))
    else:
        raise ValueError("Unknown source kind")
    return rows


def unrelated_bounty(row):
    text = row["title"] + " " + row.get("description", "")
    return bool(re.search(r"\bbount\w*\b", text, re.I) and not re.search(
        r"\b(software|bug|security|vulnerabilit\w*|developer\w*|coding|open.source|AI|hackathon\w*)\b", text, re.I))


def category(row):
    title = row["title"]
    if re.search(r"fellowship|residenc|claude corps|paid (?:training|bootcamp)|tasustatud .*õpe", title, re.I):
        return "programme"
    if re.search(r"hackathon|häkaton|bount|grant|scholarship|paid research|paid user testing|paid participants|paid project|seeking freelancer|otsin tegijat|stipendium|toetus", title, re.I):
        return "secondary"
    if row["kind"] in ("news", "hn") and not re.search(r"hiring|jobs?|career|internship|recruit|trainer|evaluator|rater|kandideeri|tööpakkumine", title, re.I):
        return "secondary"
    return "job"


def choose(candidates, limit, secondary_limit):
    ordered = sorted(candidates.items(), key=lambda p: (p[1]["score"], p[1]["published"] or 0), reverse=True)
    programmes = [p for p in ordered if category(p[1]) == "programme"]
    jobs = [p for p in ordered if category(p[1]) == "job"]
    secondary = [p for p in ordered if category(p[1]) == "secondary"]
    chosen = []
    primary_limit = limit - min(secondary_limit, len(secondary), max(0, limit - 1))
    while len(chosen) < primary_limit and (programmes or jobs):
        group = programmes if len(chosen) % 2 == 0 and programmes else jobs or programmes
        chosen.append(group.pop(0))
    return chosen + secondary[:max(0, min(secondary_limit, limit - len(chosen)))]


def rank(row, now, max_age_days=14):
    """Conservative keyword filter. Returns None or (score, location-label)."""
    title, description = row["title"], row["description"]
    text = title + " " + description
    date = row["published"]
    if date and (date < now - max_age_days * 86400 or date > now + 86400):
        return None
    if NEGATIVE.search(title) or unrelated_bounty(row):
        return None
    news = row["kind"] == "news"
    if row.get("discovery_only"):
        if not re.search(r"applications? open|apply|we.re hiring|hiring:|looking for|seeking|recruit|launch|kandideeri", title, re.I):
            return None
        if not (AI.search(title) or re.search(r"fellowship|residenc|claude corps|paid project", title, re.I)):
            return None
        if re.search(r"\b(Dubai|India|Pakistan|United States|U\.S\.)\b", title, re.I) and not (EU.search(title) or WORLD.search(title)):
            return None
    if row["kind"] == "hn":
        if not (AI.search(text) or OPPORTUNITY.search(text) or re.search(r"SEEKING FREELANCER", title, re.I)):
            return None
    if news:
        client = bool(re.search(r"looking for someone|seeking freelancer|hiring.*paid project|otsin tegijat|vajan .*tegijat", text, re.I) and PAY.search(text))
        task_job = AI.search(text) and re.search(r"trainer|evaluator|rater|annotation|labeling", text, re.I) and re.search(r"hiring|jobs|recruit", text, re.I)
        if not (task_job or client) and (not ACTION.search(text) or not OPPORTUNITY.search(text)):
            return None
        if not (task_job or client) and not (AI.search(text) or re.search(r"tech|startup|hackathon|häkaton|bount|paid research|paid user testing|paid participants|stipendium|taotlusvoor", text, re.I)):
            return None
        if not (task_job or client) and not PAY.search(text) and not re.search(r"fellowship|residenc", text, re.I):
            return None
    elif row["kind"] != "hn" and not (AI.search(title) or OPPORTUNITY.search(title)):
        # Avoid every ordinary job mentioning the company's use of AI.
        return None
    location = row["location"]
    explicit_us = re.search(r"\b(?:US|USA|U\.S\.)[- ]only\b|must (?:be |reside |live ).{0,35}(?:United States|USA)|authorized to work in (?:the )?(?:United States|USA)", text, re.I)
    # Geography is an applicability hint, not proof of work authorization.
    if location and OUTSIDE.search(location) and not (EU.search(location) or WORLD.search(location)):
        return None
    if explicit_us and not (location and (EU.search(location) or WORLD.search(location))):
        return None
    if re.search(r"\bunpaid\b|\bvolunteer position\b", text, re.I):
        return None
    if location and EU.search(location):
        region = location + " — kontrolli riigi/tööloa nõudeid"
        geo_score = 4
    elif location and WORLD.search(location):
        region = location + " — ülemaailmne märge allikas"
        geo_score = 4
    elif location:
        # For European job feed allow unspecified European city, but label uncertainty.
        if not row["remote"] and row["kind"] != "arbeitnow":
            return None
        region = location + " — Eestist sobivus kinnitamata"
        geo_score = 0
    else:
        region = "Asukoht kinnitamata; kaugtöö ei tähenda automaatselt Eestist sobivust"
        geo_score = 0
    score = 4 + geo_score + (3 if BEGINNER.search(title) else 0) + (2 if row["salary"] else 0)
    if news:
        score += 2 if re.search(r"applications open|apply now|call for|register|kandideeri", text, re.I) else 0
    return score, region


def fingerprint(row):
    # Cross-source job duplicates often have tracking links but identical title/company.
    title = re.sub(r"\W+", " ", row["title"].casefold()).strip()
    company = re.sub(r"\W+", " ", row["company"].casefold()).strip()
    if row.get("discovery_only"):
        return m.digest("social|" + title)
    return m.digest(title + "|" + company) if company else m.digest(row["url"])


def message(row):
    news = row["kind"] == "news"
    label = "UUS OTSINGULEID — vajab kontrolli" if news else "LEITUD TÖÖ / PROGRAMM — kontrolli kuulutust"
    date = datetime.fromtimestamp(row["published"], timezone.utc).strftime("%Y-%m-%d") if row["published"] else "pole avaldatud"
    title = row["title"].replace("@", "＠")
    group = {"programme": "PÕHIFOOKUS · PROGRAMM", "job": "PÕHIFOOKUS · TÖÖ", "secondary": "LISAVÕIMALUS"}[category(row)]
    lines = [group, label, title]
    if row["company"]:
        lines.append("Ettevõte: " + row["company"])
    lines.extend(["Asukoht: " + row["region"], "Avaldatud: " + date, "Allikas: " + row["source"]])
    if row["salary"]:
        lines.append("Tasu allikas: " + row["salary"])
    else:
        lines.append("Tasu: vaata kuulutusest; summa kinnitamata")
    if news:
        lines.append("Uudis/otsingutulemus, mitte kontrollitud avatud kandideerimisvorm.")
    if row["source_id"] == "remotive":
        lines.append("Remotive avalik voog võib hilineda 24 tundi.")
    if row.get("discovery_only"):
        lines.append("Sotsiaalmeedia vihje: kontrolli algset pakkumist; video/postitus ei tõenda avatud kohta.")
    lines.append("Järgmine samm: ava link ning kontrolli tähtaega, tasu ja kandideerimisnõudeid.")
    body = "\n".join(lines)
    # monitor.enqueue has an 1800-character budget including its timestamp.
    return body[:max(0, 1730 - len(row["url"]))] + "\n" + row["url"]


def fetch(session, source):
    url = source.get("url")
    if source["kind"] == "news":
        url = "https://news.google.com/rss/search?" + urlencode({"q": source["query"], "hl": "en-US", "gl": "US", "ceid": "US:en"})
    if source["kind"] == "hn":
        url = "https://hn.algolia.com/api/v1/search_by_date?" + urlencode({
            "query": source["query"], "tags": "comment", "hitsPerPage": 100,
            "numericFilters": "created_at_i>" + str(int(time.time() - 14 * 86400))})
    with session.get(url, timeout=(10, 25), stream=True) as response:
        if response.status_code in (429, 503):
            raise m.FetchError(f"HTTP {response.status_code}", max(3600, m.retry_seconds(response.headers.get("Retry-After"))))
        response.raise_for_status()
        chunks, size, started = [], 0, time.monotonic()
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 5_000_000 or time.monotonic() - started > 45:
                raise m.FetchError("Response exceeded limit")
            chunks.append(chunk)
    return parse(source, b"".join(chunks))


def run(config, path, report_path, dry_run=False):
    now = time.time()
    destinations = {"preview": True} if dry_run else m.channels()
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
        "version": 1, "seen": {}, "sources": {}, "candidates": {}, "outbox": [],
        "delivery_after": {}, "daily": {"day": "", "sent": 0}, "initialized": False}
    if state.get("version") != 1:
        raise ValueError("Unsupported discovery state")
    report = {"checked_at": m.utc(), "sources": [], "selected": []}
    for row in state["candidates"].values():
        m.archive_offer(state, row)
    with requests.Session() as session:
        session.headers.update({"User-Agent": "OpportunityWatch/1.0", "Accept-Language": "en"})
        if not dry_run:
            m.flush(session, state, destinations, path)
        for source in config["sources"]:
            record = state["sources"].setdefault(source["id"], {})
            if now < record.get("next_check", 0):
                continue
            try:
                rows = fetch(session, source)
            except (requests.RequestException, m.FetchError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
                failures = record.get("failures", 0) + 1
                record.update(failures=failures, next_check=now + max(source["interval_seconds"], getattr(exc, "retry_after", 0)))
                report["sources"].append({"id": source["id"], "error": type(exc).__name__})
                logging.error("Discovery source %s failed: %s", source["id"], type(exc).__name__)
                if failures == 3:
                    m.enqueue(state, destinations, "OTSINGU TÕRGE: " + source["name"] + ". Kolm kontrolli ebaõnnestus; teised allikad jätkavad.")
            else:
                if record.get("failures", 0) >= 3:
                    m.enqueue(state, destinations, "OTSING TAASTUS: " + source["name"])
                accepted = 0
                for row in rows:
                    key = fingerprint(row)
                    if key in state["seen"]:
                        continue
                    rating = rank(row, now, config["max_age_days"])
                    state["seen"][key] = now
                    if rating:
                        row["score"], row["region"] = rating
                        row["found_at"] = now
                        # Do not persist full scraped descriptions in a public repository.
                        row.pop("description")
                        row["category"] = category(row)
                        m.archive_offer(state, row)
                        state["candidates"][key] = row
                        accepted += 1
                record.update(failures=0, next_check=now + source["interval_seconds"], last_success=m.utc(), items=len(rows), accepted=accepted)
                report["sources"].append({"id": source["id"], "items": len(rows), "new_candidates": accepted})
                logging.info("%s: %d items, %d new relevant candidates", source["id"], len(rows), accepted)
            if not dry_run:
                m.save(path, state)
            time.sleep(1)
        today = datetime.now(timezone.utc).date().isoformat()
        if state["daily"]["day"] != today:
            state["daily"] = {"day": today, "sent": 0, "secondary": 0}
        for key, row in list(state["candidates"].items()):
            if NEGATIVE.search(row["title"]) or unrelated_bounty(row) or (row["published"] or row["found_at"]) < now - config["max_age_days"] * 86400:
                del state["candidates"][key]
        limit = config["alerts_per_run"] if state["initialized"] else config["initial_alerts"]
        limit = max(0, min(limit, config["alerts_per_day"] - state["daily"]["sent"]))
        secondary_left = max(0, config.get("secondary_per_day", 4) - state["daily"].get("secondary", 0))
        chosen = choose(state["candidates"], limit, min(config.get("secondary_per_run", 1), secondary_left))
        for key, row in chosen:
            m.enqueue(state, destinations, message(row), row)
            report["selected"].append(row)
            del state["candidates"][key]
            state["daily"]["sent"] += 1
            if category(row) == "secondary":
                state["daily"]["secondary"] = state["daily"].get("secondary", 0) + 1
        state["initialized"] = True
        state["last_run_utc"] = m.utc()
        state["seen"] = {key: seen for key, seen in state["seen"].items() if seen > now - 180 * 86400}
        report["queued_candidates"] = len(state["candidates"])
        if not dry_run:
            m.save(path, state)
            pending = m.flush(session, state, destinations, path)
        else:
            pending = False
        m.save(report_path, report)
        all_failed = bool(report["sources"]) and all("error" in result for result in report["sources"])
        return int(pending or all_failed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("discovery_config.json"))
    parser.add_argument("--state", type=Path, default=Path("discovery-state.json"))
    parser.add_argument("--report", type=Path, default=Path("discovery-report.json"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    with m.lock(args.state.with_suffix(".lock")):
        return run(config, args.state, args.report, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
