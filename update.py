from pathlib import Path
from datetime import datetime
import logging
import json
import re
import requests
from icalendar import Calendar, Event
from zoneinfo import ZoneInfo
from collections import defaultdict

URL = "https://www.nordwestbahn.de/de/service/deine-reiseplanung/meldungen"
OUT_DIR = Path("feeds")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("scraper")


# ---------------- NORMALIZE PDF ----------------
def normalize_pdf_url(url: str) -> str:
    if not url:
        return ""

    url = url.replace("\\u002F", "/").replace("\\/", "/")
    url = requests.utils.unquote(url)

    # extract stable schedule path
    m = re.search(r"schedule/\d+/.+\.pdf", url)
    if not m:
        return url

    return f"https://download.transdev.de/transdev/uploads/nwb/{m.group(0)}"


# ---------------- EXTRACT ISO TIMES ----------------
ISO_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}"
)

def extract_times(text: str):
    if not text:
        return None, None

    matches = ISO_RE.findall(text)
    if len(matches) < 2:
        return None, None

    try:
        start = datetime.fromisoformat(matches[0])
        end = datetime.fromisoformat(matches[1])
        return start, end
    except Exception:
        return None, None


# ---------------- EXTRACT LINE ----------------
def extract_line(text: str):
    if not text:
        return "UNKNOWN"

    t = text.upper()
    t = re.sub(r"https?://\S+", " ", t)

    match = re.search(r"\b(RS|RB|RE)\s?-?\s?\d+\b", t)
    if not match:
        return "UNKNOWN"

    return match.group(0).replace(" ", "").replace("-", "")


# ---------------- CLEAN DESCRIPTION ----------------
def extract_description(text: str):
    if not text:
        return ""

    # remove everything after PDF marker or obvious footer noise
    cut_markers = [
        "PDF-DOKUMENT",
        "HERUNTERLADEN",
        "DOWNLOAD",
    ]

    for marker in cut_markers:
        idx = text.upper().find(marker)
        if idx != -1:
            text = text[:idx]

    return text.strip()


# ---------------- FETCH (NO SLICING, NO HACKS) ----------------
def fetch():
    log.info(f"Requesting {URL}")

    raw = requests.get(URL, timeout=30).text
    raw = raw.replace("\\u002F", "/").replace("\\r\\n", "\n")

    # find all PDFs
    pdf_pattern = re.compile(r"https://[^\"'\s]+?\.pdf")

    seen = set()
    items = []

    for match in pdf_pattern.finditer(raw):
        pdf = normalize_pdf_url(match.group(0))

        if pdf in seen:
            continue
        seen.add(pdf)

        items.append({
            "pdf": pdf,
            "raw": raw  # shared reference only (NOT sliced)
        })

    log.info(f"Unique PDF events: {len(items)}")
    return items


# ---------------- BUILD CALENDAR ----------------
def build_calendar(items):
    cal = Calendar()
    cal.add("prodid", "-//NWB Baustellen//")
    cal.add("version", "2.0")

    for item in items:
        text = item["raw"]
        pdf = item["pdf"]

        start, end = extract_times(text)
        if not start or not end:
            continue

        line = extract_line(text)
        desc = extract_description(text)

        event = Event()
        event.add("summary", f"{line} – Baustelle")
        event.add("dtstart", start.astimezone(ZoneInfo("Europe/Berlin")))
        event.add("dtend", end.astimezone(ZoneInfo("Europe/Berlin")))
        event.add("uid", pdf)
        event.add(
            "description",
            f"{desc}\n\nErsatzfahrplan:\n{pdf}"
        )
        event.add("categories", [line])

        cal.add_component(event)

    return cal


# ---------------- SAVE ----------------
def save_calendar(cal):
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / "baustellen.ics"

    path.write_bytes(cal.to_ical())
    log.info(f"Wrote {path}")


# ---------------- MAIN ----------------
def main():
    items = fetch()
    cal = build_calendar(items)
    save_calendar(cal)
    log.info("Done")


if __name__ == "__main__":
    main()
