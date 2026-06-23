from bs4 import BeautifulSoup
import requests
import json
from pathlib import Path
from datetime import datetime, timedelta
from icalendar import Calendar, Event
import re
import logging

URL = "https://www.nordwestbahn.de/de/service/deine-reiseplanung/meldungen?"

ICS_FILE = "baustellen.ics"
KNOWN_FILE = "known.json"

# ---------------- LOGGING ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

log = logging.getLogger(__name__)
# ----------------------------------------


def load_known():
    if Path(KNOWN_FILE).exists():
        log.info("Loading known.json")
        return json.loads(Path(KNOWN_FILE).read_text())
    log.info("No known.json found, starting fresh")
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
    cal.add('prodid', '-//NWB Baustellen//')
    cal.add('version', '2.0')
    return cal


def save_calendar(cal):
    Path(ICS_FILE).write_bytes(cal.to_ical())
    log.info("Saved ICS file")


def fetch():
    log.info(f"Requesting: {URL}")

    r = requests.get(URL, timeout=30)

    log.info(f"HTTP status: {r.status_code}")
    log.info(f"Response size: {len(r.text)} characters")

    raw = r.text

    # Debug: confirm whether PDFs exist at all
    pdf_hint_count = raw.count(".pdf")
    log.info(f"'.pdf' occurrences in raw HTML: {pdf_hint_count}")

    # Show a small preview for sanity check
    log.debug(f"HTML preview:\n{raw[:500]}")

    # Match multiple escaping styles
    pattern = r"https:\\?u002F\\?u002F[^\"'\s]+?\.pdf"

    matches = re.findall(pattern, raw)

    log.info(f"Regex matches found: {len(matches)}")

    results = []
    seen = set()

    for m in matches:
        url = (
            m.replace("\\u002F", "/")
             .replace("\\/","/")
             .replace("\\\\u002F","/")
        )

        if url not in seen:
            seen.add(url)
            results.append({"pdf": url})
            log.info(f"Found PDF: {url}")

    log.info(f"Total unique PDFs: {len(results)}")

    return results


# ---------------- MAIN ----------------

known = load_known()
cal = load_calendar()

log.info(f"Loaded {len(known)} known entries")

new_count = 0

for item in fetch():

    uid = item["pdf"]

    if uid in known:
        log.info(f"Skipping known: {uid}")
        continue

    log.info(f"New item found: {uid}")

    event = Event()

    # safety check
    title = item.get("title", "Baustelle")
    event.add('summary', title)

    start = datetime.now()
    end = start + timedelta(days=1)

    event.add('dtstart', start)
    event.add('dtend', end)

    event.add(
        'description',
        f"Ersatzfahrplan:\n{item['pdf']}"
    )

    event.add('uid', uid)

    cal.add_component(event)

    known.append(uid)
    new_count += 1

log.info(f"Added {new_count} new events")

save_calendar(cal)
save_known(known)

log.info("Done")
