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
KNOWN_FILE = "known.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("scraper")


# ---------------- MONTH MAP ----------------
GERMAN_MONTHS = {
    "Januar": 1,
    "Februar": 2,
    "März": 3,
    "Maerz": 3,
    "April": 4,
    "Mai": 5,
    "Juni": 6,
    "Juli": 7,
    "August": 8,
    "September": 9,
    "Oktober": 10,
    "November": 11,
    "Dezember": 12,
}


# ---------------- LOAD / SAVE ----------------
def load_known():
    p = Path(KNOWN_FILE)
    if not p.exists():
        return []

    try:
        content = p.read_text().strip()
        if not content:
            return []
        return json.loads(content)
    except json.JSONDecodeError:
        log.warning("known.json corrupted → resetting")
        return []


def save_known(data):
    Path(KNOWN_FILE).write_text(json.dumps(data, indent=2))


def load_calendar():
    p = Path(ICS_FILE)
    if p.exists():
        return Calendar.from_ical(p.read_bytes())

    cal = Calendar()
    cal.add("prodid", "-//NWB Baustellen//")
    cal.add("version", "2.0")
    return cal


def save_calendar(cal):
    Path(ICS_FILE).write_bytes(cal.to_ical())


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


# ---------------- ZEITRAUM PARSING ----------------
def parse_zeitraum(text: str):
    pattern = (
        r"(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\s*(\d{4})\s*"
        r"(\d{1,2}:\d{2})\s*bis\s*"
        r"(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\s*(\d{4})\s*"
        r"(\d{1,2}:\d{2})"
    )

    m = re.search(pattern, text)
    if not m:
        return None, None

    d1, m1, y1, t1, d2, m2, y2, t2 = m.groups()

    def build(d, mon, y, t):
        month = GERMAN_MONTHS.get(mon)
        if not month:
            return None

        h, mi = map(int, t.split(":"))

        return datetime(
            int(y),
            month,
            int(d),
            h,
            mi,
            tzinfo=ZoneInfo("Europe/Berlin")
        )

    return build(d1, m1, y1, t1), build(d2, m2, y2, t2)


def extract_zeitraum(raw: str):
    m = re.search(r"Zeitraum:\s*(.+)", raw)
    return m.group(1) if m else None


# ---------------- FETCH ----------------
def fetch():
    log.info(f"Requesting {URL}")

    r = requests.get(URL, timeout=30)
    raw = r.text

    log.info(f"HTTP {r.status_code} | size={len(raw)}")

    matches = re.findall(r"https:\\?u002F\\?u002F[^\"'\s]+?\.pdf", raw)

    log.info(f"PDF matches found: {len(matches)}")

    results = []
    seen = set()

    for m in matches:
        url = normalize_pdf_url(m)

        if url not in seen:
            seen.add(url)
            results.append({
                "pdf": url,
                "raw": raw
            })

    return results


# ---------------- MAIN ----------------
known = load_known()
cal = load_calendar()

log.info(f"Loaded known: {len(known)}")

new = 0

for item in fetch():

    pdf = item["pdf"]
    raw = item["raw"]

    if pdf in known:
        continue

    log.info(f"New: {pdf}")

    event = Event()
    event.add("summary", "Baustellenmeldung")

    zeitraum = extract_zeitraum(raw)

    start = end = None

    if zeitraum:
        start, end = parse_zeitraum(zeitraum)

    if not start or not end:
        log.warning("No valid Zeitraum → fallback")
        start = datetime.now(tz=ZoneInfo("Europe/Berlin"))
        end = start

    event.add("dtstart", start)
    event.add("dtend", end)
    event.add("uid", pdf)
    event.add("description", f"Ersatzfahrplan:\n{pdf}")

    cal.add_component(event)

    known.append(pdf)
    new += 1


log.info(f"Added {new} events")

save_calendar(cal)
save_known(known)

log.info("Done")
