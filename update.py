from pathlib import Path
from datetime import datetime, timedelta
import json
import re
import logging

import requests
from icalendar import Calendar, Event


# ---------------- CONFIG ----------------
URL = "https://www.nordwestbahn.de/de/service/deine-reiseplanung/meldungen"

ICS_FILE = "baustellen.ics"
KNOWN_FILE = "known.json"

# ---------------- LOGGING ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

log = logging.getLogger("scraper")


# ---------------- STORAGE ----------------
def load_known():
    path = Path(KNOWN_FILE)

    if not path.exists():
        log.info("No known.json found")
        return []

    try:
        content = path.read_text().strip()

        if not content:
            log.warning("known.json is empty → resetting")
            return []

        return json.loads(content)

    except json.JSONDecodeError:
        log.warning("known.json is corrupted → resetting")
        return []


def save_known(data):
    Path(KNOWN_FILE).write_text(json.dumps(data, indent=2))
    log.info(f"Saved {len(data)} known entries")


def load_calendar():
    if Path(ICS_FILE).exists():
        log.info("Loading existing ICS file")
        return Calendar.from_ical(Path(ICS_FILE).read_bytes())

    log.info("Creating new calendar")
    cal = Calendar()
    cal.add("prodid", "-//NWB Baustellen//")
    cal.add("version", "2.0")
    return cal


def save_calendar(cal):
    Path(ICS_FILE).write_bytes(cal.to_ical())
    log.info("Saved ICS file")


# ---------------- URL NORMALIZATION ----------------
def normalize_pdf_url(url: str) -> str:
    url = url.replace("\\u002F", "/").replace("\\/", "/")

    if "s3.storage.planetary-networks.de" in url:
        url = url.replace(
            "s3.storage.planetary-networks.de",
            "download.transdev.de"
        )

    return url


# ---------------- DATE PARSING ----------------
DATE_PATTERN = re.compile(
    r"vom-(\d{2})-(\d{2})-bis-(\d{2})-(\d{2})-(\d{4})"
)

def extract_dates(url: str):
    match = DATE_PATTERN.search(url)
    if not match:
        return None, None

    d1, m1, d2, m2, year = match.groups()

    start = datetime(int(year), int(m1), int(d1))
    end = datetime(int(year), int(m2), int(d2))

    return start, end


# ---------------- FETCH ----------------
def fetch():
    log.info(f"Requesting {URL}")

    r = requests.get(URL, timeout=30)

    log.info(f"HTTP status: {r.status_code}")
    log.info(f"Response size: {len(r.text)} chars")

    raw = r.text

    # broad match for escaped PDF URLs
    matches = re.findall(r"https:\\?u002F\\?u002F[^\"'\s]+?\.pdf", raw)

    log.info(f"Raw PDF matches: {len(matches)}")

    results = []
    seen = set()

    for m in matches:
        url = normalize_pdf_url(m)

        if url not in seen:
            seen.add(url)
            results.append({"pdf": url})

    log.info(f"Unique PDFs: {len(results)}")

    return results


# ---------------- MAIN ----------------
known = load_known()
cal = load_calendar()

log.info(f"Loaded {len(known)} known entries")

new_count = 0

for item in fetch():
    pdf = item["pdf"]

    if pdf in known:
        log.info(f"Skipping known: {pdf}")
        continue

    log.info(f"New PDF found: {pdf}")

    event = Event()

    # title fallback
    event.add("summary", "Baustellenmeldung")

    # extract real dates from filename if possible
    start, end = extract_dates(pdf)

    if start and end:
        log.info(f"Parsed dates: {start.date()} → {end.date()}")
    else:
        log.warning("No date found in filename, using fallback")
        start = datetime.now()
        end = start + timedelta(days=1)

    event.add("dtstart", start)
    event.add("dtend", end)

    event.add(
        "description",
        f"Ersatzfahrplan:\n{pdf}"
    )

    event.add("uid", pdf)

    cal.add_component(event)

    known.append(pdf)
    new_count += 1


log.info(f"Added {new_count} new events")

save_calendar(cal)
save_known(known)

log.info("Done")
