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


# ---------------- TIME PARSING (NEW SYSTEM) ----------------
START_RE = re.compile(r'"starts_at":"([^"]+)"')
END_RE = re.compile(r'"ends_at":"([^"]+)"')


def parse_iso(dt: str):
    if not dt:
        return None
    return datetime.fromisoformat(dt)


def extract_times(raw: str):
    start_m = START_RE.search(raw)
    end_m = END_RE.search(raw)

    start = parse_iso(start_m.group(1)) if start_m else None
    end = parse_iso(end_m.group(1)) if end_m else None

    return start, end


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

    # ✅ REAL TIME SOURCE (THIS IS THE FIX)
    start, end = extract_times(raw)

    if not start or not end:
        log.warning("Missing starts_at/ends_at → fallback used")
        start = datetime.now(tz=ZoneInfo("Europe/Berlin"))
        end = start

    else:
        log.info(f"Event time: {start} → {end}")

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
