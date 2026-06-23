from pathlib import Path
from datetime import datetime
import json
import re
import logging
import requests
from icalendar import Calendar, Event
from zoneinfo import ZoneInfo


URL = "https://www.nordwestbahn.de/de/service/deine-reiseplanung/meldungen"

ICS_FILE = "baustellen.ics"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("scraper")


# ---------------- URL NORMALIZATION ----------------
def normalize_pdf_url(url: str) -> str:
    url = url.replace("\\u002F", "/").replace("\\/", "/")

    url = url.replace(
        "s3.storage.planetary-networks.de",
        "download.transdev.de"
    )

    url = requests.utils.unquote(url)
    url = url.replace(" ", "")

    return url


# ---------------- TIME EXTRACTION ----------------
START_RE = re.compile(r'"starts_at":"([^"]+)"')
END_RE = re.compile(r'"ends_at":"([^"]+)"')


def parse_iso(dt: str):
    return datetime.fromisoformat(dt) if dt else None


def extract_times(raw: str):
    # find all ISO timestamps in order
    times = ISO_RE.findall(raw)

    if len(times) < 2:
        return None, None

    start = datetime.fromisoformat(times[0])
    end = datetime.fromisoformat(times[1])

    return start, end


# ---------------- FETCH ----------------
def fetch():
    log.info(f"Requesting {URL}")

    r = requests.get(URL, timeout=30)
    raw = r.text

    log.info(f"HTTP {r.status_code} | size={len(raw)}")

    matches = re.findall(r"https:\\?u002F\\?u002F[^\"'\s]+?\.pdf", raw)

    log.info(f"PDF matches: {len(matches)}")

    seen = set()
    items = []

    for m in matches:
        url = normalize_pdf_url(m)

        if url not in seen:
            seen.add(url)
            items.append({
                "pdf": url,
                "raw": raw
            })

    return items


# ---------------- BUILD ICS ----------------
def build_calendar(items):
    cal = Calendar()
    cal.add("prodid", "-//NWB Baustellen//")
    cal.add("version", "2.0")

    uids = []

    for item in items:
        pdf = item["pdf"]
        raw = item["raw"]

        log.info(f"Processing {pdf}")

        event = Event()
        event.add("summary", "Baustellenmeldung")

        start, end = extract_times(raw)

        if not start or not end:
            log.warning("Missing timestamps → fallback")
            start = datetime.now(tz=ZoneInfo("Europe/Berlin"))
            end = start

        event.add("dtstart", start)
        event.add("dtend", end)
        event.add("uid", pdf)
        event.add("description", f"Ersatzfahrplan:\n{pdf}")

        cal.add_component(event)

        uids.append(pdf)

    return cal, uids


# ---------------- SAVE ----------------
def save_calendar(cal):
    Path(ICS_FILE).write_bytes(cal.to_ical())
    log.info("ICS written")


def save_debug(uids):
    Path("debug_uids.json").write_text(json.dumps(uids, indent=2))


# ---------------- MAIN ----------------
items = fetch()

cal, uids = build_calendar(items)

save_calendar(cal)
save_debug(uids)

log.info(f"Rebuilt ICS with {len(uids)} events")
