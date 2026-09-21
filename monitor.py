"""Public HTML opportunity monitor. Python 3.11+. See README.md before deployment."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import random
import re
import time
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

LOG = logging.getLogger("watch")
AGENT = "OpportunityWatch"
OPEN = [r"\bapply now\b", r"\bapplications (?:are )?open\b", r"\bopen cohort\b"]
CLOSED = [r"\bapplications (?:are )?closed\b", r"\bsign up for updates\b",
          r"\bapplications (?:are )?not (?:yet )?open\b"]


class FetchError(Exception):
    def __init__(self, message, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after


def utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def save(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp, path)


@contextmanager
def lock(path):
    """OS releases the lock on crashes; the empty lock file may remain."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as file:
        file.seek(0)
        if not file.read(1):
            file.write(b"0")
            file.flush()
        file.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            file.seek(0)
            if os.name == "nt":
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(file, fcntl.LOCK_UN)


def retry_seconds(value):
    try:
        return max(0, float(value))
    except (TypeError, ValueError):
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return 0


def extract(html, target):
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select("script,style,noscript,template"):
        node.decompose()
    for selector in target.get("ignore_selectors", []):
        for node in soup.select(selector):
            node.decompose()
    nodes = soup.select(target.get("selector", "body"))
    if not nodes:
        raise FetchError("Configured CSS selector matched no elements")
    text = " ".join(" ".join(n.stripped_strings) for n in nodes)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise FetchError("Selected content is empty; possibly JavaScript-rendered")
    opened = any(re.search(p, text, re.I) for p in target.get("open_patterns", OPEN))
    closed = any(re.search(p, text, re.I) for p in target.get("closed_patterns", CLOSED))
    status = "MIXED" if opened and closed else "OPEN" if opened else "CLOSED" if closed else "UNKNOWN"
    # Include destinations so unchanged 'Apply' text with a new URL is detected.
    links = sorted({urljoin(target["url"], a["href"])
                    for n in nodes for a in n.select("a[href]")
                    if urlsplit(urljoin(target["url"], a["href"])).scheme in ("http", "https")})
    content = "\n".join(str(n) for n in nodes) if target.get("mode", "text") == "html" else text + "\n" + "\n".join(links)
    return {"hash": digest(content), "status": status}


class Fetcher:
    def __init__(self, session, state):
        self.session, self.state = session, state
        self.robots = {}
        self.last = {}

    def get(self, url, delay=2):
        origin = "{0.scheme}://{0.netloc}".format(urlsplit(url))
        hold = self.state["hosts"].get(origin, 0) - time.time()
        if hold > 0:
            raise FetchError("Host is in Retry-After cooldown", hold)
        time.sleep(max(0, delay - (time.monotonic() - self.last.get(origin, 0))))
        start = time.monotonic()
        try:
            with self.session.get(url, timeout=(10, 25), stream=True) as response:
                if response.status_code in (429, 503):
                    wait = max(300, retry_seconds(response.headers.get("Retry-After")))
                    self.state["hosts"][origin] = time.time() + wait
                    raise FetchError(f"HTTP {response.status_code}", wait)
                if response.status_code >= 400:
                    raise FetchError(f"HTTP {response.status_code}")
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > 3_000_000 or time.monotonic() - start > 45:
                        raise FetchError("Response exceeded size/time limit")
                    chunks.append(chunk)
                return b"".join(chunks), response.encoding or "utf-8", response.headers.get("Content-Type", "")
        finally:
            self.last[origin] = time.monotonic()

    def page(self, url):
        origin = "{0.scheme}://{0.netloc}".format(urlsplit(url))
        if origin not in self.robots:
            parser = RobotFileParser()
            try:
                body, encoding, _ = self.get(origin + "/robots.txt")
                parser.parse(body.decode(encoding, errors="replace").splitlines())
            except FetchError as exc:
                if str(exc) == "HTTP 404":
                    parser.parse([])
                else:
                    raise
            self.robots[origin] = parser
        parser = self.robots[origin]
        if not parser.can_fetch(AGENT, url):
            raise FetchError("robots.txt disallows this URL")
        delay = max(2, parser.crawl_delay(AGENT) or 0)
        rate = parser.request_rate(AGENT)
        if rate:
            delay = max(delay, rate.seconds / rate.requests)
        if delay > 60:
            raise FetchError("robots.txt requires >60s pacing; use another source")
        body, encoding, content_type = self.get(url, delay)
        if "html" not in content_type.lower():
            raise FetchError("Expected HTML response")
        return body.decode(encoding, errors="replace")


def channels():
    result = {}
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
    if webhook:
        parsed = urlsplit(webhook)
        if parsed.scheme != "https" or parsed.hostname not in ("discord.com", "discordapp.com") or not parsed.path.startswith("/api/webhooks/"):
            raise ValueError("Invalid Discord webhook URL")
        result["discord"] = webhook
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if bool(token) != bool(chat):
        raise ValueError("Set both Telegram secrets")
    if token:
        result["telegram"] = (token, chat)
    if not result:
        raise ValueError("Configure at least one alert channel in environment variables")
    return result


def archive_offer(state, opportunity):
    key = digest(opportunity["url"])
    archive = state.setdefault("opportunities", {})
    row = archive.setdefault(key, {"first_seen_at": utc()})
    row.update({k: v for k, v in opportunity.items() if k not in ("description",)})
    row["reference"] = "OW-" + key[:10].upper()
    return key


def enqueue(state, destinations, message, opportunity=None):
    event = {"text": (utc() + "\n" + message)[:1800], "pending": list(destinations)}
    if opportunity:
        key = archive_offer(state, opportunity)
        event["opportunity_id"] = key
        row = state["opportunities"][key]
        if "discord" in destinations:
            row["discord_pending"] = True
        # A short shared reference lets users find a notification in the local archive.
        event["text"] = (utc() + "\n" + row["reference"] + "\n" + message)[:1800]
    state["outbox"].append(event)


def deliver(session, channel, destination, message):
    if channel == "discord":
        response = session.post(destination, params={"wait": "true"},
                                json={"content": message, "allowed_mentions": {"parse": []}},
                                timeout=(10, 25), allow_redirects=False)
    else:
        token, chat = destination
        response = session.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                json={"chat_id": chat, "text": message,
                                      "link_preview_options": {"is_disabled": True}},
                                timeout=(10, 25), allow_redirects=False)
    with response:
        if response.status_code == 429:
            wait = retry_seconds(response.headers.get("Retry-After"))
            try:
                body = response.json()
                wait = max(wait, float(body.get("retry_after", 0)),
                           float(body.get("parameters", {}).get("retry_after", 0)))
            except (ValueError, TypeError):
                pass
            raise FetchError("Alert rate limited", max(60, wait))
        if not 200 <= response.status_code < 300:
            raise FetchError(f"Alert HTTP {response.status_code}")
        if channel == "telegram" and not response.json().get("ok"):
            raise FetchError("Telegram rejected message")
        if channel == "discord":
            try:
                receipt = response.json()
                if isinstance(receipt, dict) and all(str(receipt.get(k, '')).isdigit() for k in ('id', 'channel_id')):
                    return {"message_id": str(receipt['id']), "channel_id": str(receipt['channel_id'])}
            except ValueError:
                pass


def flush(session, state, destinations, path):
    blocked = set()
    for event in list(state["outbox"]):
        for channel in list(event["pending"]):
            if channel in blocked or time.time() < state["delivery_after"].get(channel, 0):
                continue
            if channel not in destinations:
                LOG.error("Pending message needs removed channel: %s", channel)
                blocked.add(channel)
                continue
            try:
                receipt = deliver(session, channel, destinations[channel], event["text"])
            except (requests.RequestException, FetchError, ValueError) as exc:
                # Do not log exceptions containing webhook URLs or Telegram tokens.
                LOG.error("%s alert failed (%s); queued for retry", channel, type(exc).__name__)
                state["delivery_after"][channel] = time.time() + max(60, getattr(exc, "retry_after", 0))
                blocked.add(channel)
            else:
                event["pending"].remove(channel)
                row = state.get("opportunities", {}).get(event.get("opportunity_id"))
                if row is not None and channel == "discord":
                    row.update(discord_sent_at=utc(), discord_pending=False, delivery_evidence="api_success")
                    if isinstance(receipt, dict):
                        row["discord_url"] = "https://discord.com/channels/787327675949121567/" + receipt['channel_id'] + "/" + receipt['message_id']
            save(path, state)
        if not event["pending"]:
            state["outbox"].remove(event)
            save(path, state)
    return bool(state["outbox"])


def check(target, fetcher, state, destinations):
    record = state["targets"].setdefault(target["id"], {})
    if time.time() < record.get("next_check", 0):
        return bool(record.get("failures"))
    try:
        current = extract(fetcher.page(target["url"]), target)
    except (requests.RequestException, FetchError) as exc:
        failures = record.get("failures", 0) + 1
        record["failures"] = failures
        record["next_check"] = time.time() + max(min(3600, 60 * 2 ** min(failures, 6)), getattr(exc, "retry_after", 0))
        detail = str(exc) if isinstance(exc, FetchError) else type(exc).__name__
        LOG.error("%s fetch failed (%s), attempt %d", target["id"], detail, failures)
        if failures == 3:
            enqueue(state, destinations, f"MONITOR ERROR: {target['name']}\nThree consecutive checks failed. Inspect Actions logs/source/selector.\n{target['url']}")
        return True
    signature = digest(json.dumps(target, sort_keys=True))
    old = record.get("snapshot") if record.get("config") == signature else None
    if record.get("failures", 0) >= 3:
        enqueue(state, destinations, f"MONITOR RECOVERED: {target['name']}\n{target['url']}")
    reason = None
    if old is None:
        reason = "BASELINE (first check or configuration changed)"
    elif current["status"] != old["status"]:
        reason = f"STATUS: {old['status']} -> {current['status']}"
    elif current["hash"] != old["hash"] and target.get("alert_on_change", True):
        reason = "CONTENT CHANGED"
    if reason:
        enqueue(state, destinations, f"{reason}: {target['name']}\nSignal: {current['status']} (verify on page)\n{target['url']}",
                {"title": target['name'], "url": target['url'], "kind": "page_monitor", "source": "Application page monitor", "signal": current['status']})
    record.update(snapshot=current, config=signature, failures=0, next_check=0)
    LOG.info("%s: %s%s", target["id"], current["status"], " / " + reason if reason else " / unchanged")
    return False


def load_config(path):
    config = json.loads(path.read_text(encoding="utf-8"))
    if not 300 <= config.get("interval_seconds", 600) <= 900:
        raise ValueError("interval_seconds must be between 300 and 900")
    ids = set()
    if not config.get("targets"):
        raise ValueError("Add at least one target")
    for target in config["targets"]:
        if target["id"] in ids:
            raise ValueError("Target IDs must be unique")
        ids.add(target["id"])
        if not target.get("name") or urlsplit(target["url"]).scheme != "https":
            raise ValueError("Each target needs a name and HTTPS URL")
        if target.get("mode", "text") not in ("text", "html"):
            raise ValueError("mode must be text or html")
        BeautifulSoup("", "html.parser").select(target.get("selector", "body"))
        for selector in target.get("ignore_selectors", []):
            BeautifulSoup("", "html.parser").select(selector)
        for pattern in target.get("open_patterns", OPEN) + target.get("closed_patterns", CLOSED):
            re.compile(pattern)
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--state", type=Path, default=Path("state.json"))
    parser.add_argument("--loop", action="store_true", help="Run continuously on an always-on host")
    parser.add_argument("--test-alert", action="store_true", help="Send a real test notification and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config, destinations = load_config(args.config), channels()
    with lock(args.state.with_suffix(args.state.suffix + ".lock")), requests.Session() as session:
        if args.state.exists():
            state = json.loads(args.state.read_text(encoding="utf-8"))
            if state.get("version") != 1:
                raise ValueError("Unsupported state version; restore a valid state file")
        else:
            state = {"version": 1, "targets": {}, "outbox": [], "hosts": {}, "delivery_after": {}}
        if config.get("enabled") is False:
            state["outbox"] = []
            state["last_run_utc"] = utc()
            save(args.state, state)
            return 0
        session.headers.update({"User-Agent": os.getenv("MONITOR_USER_AGENT", "OpportunityWatch/1.0"),
                                "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en"})
        if args.test_alert:
            enqueue(state, destinations, "TEST: OpportunityWatch notifications are configured.")
            save(args.state, state)
            return int(flush(session, state, destinations, args.state))
        while True:
            failed = flush(session, state, destinations, args.state)
            fetcher = Fetcher(session, state)
            for target in config["targets"]:
                failed = check(target, fetcher, state, destinations) or failed
                # Persist event and snapshot together BEFORE attempting delivery.
                save(args.state, state)
                failed = flush(session, state, destinations, args.state) or failed
            state["last_run_utc"] = utc()
            save(args.state, state)
            if not args.loop:
                return int(failed)
            time.sleep(config["interval_seconds"] + random.uniform(0, 15))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        # Never print potentially secret-bearing network exception strings.
        LOG.error("Fatal %s; check configuration, credentials, state and file permissions", type(exc).__name__)
        raise SystemExit(1)
