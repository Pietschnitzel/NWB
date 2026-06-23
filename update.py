from pathlib import Path
from datetime import datetime
import logging
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


# ---------------- URL NORMALIZATION ----------------
def normalize_pdf_url(url: str) -> str:
    if not url:
        return ""

    url = url.replace("\\u002F", "/").replace("\\/", "/")
    url = requests.utils.unquote(url)

    m = re.search(r"schedule/\d+/.+\.pdf", url)
    if not m:
        return url

    return f"https://download.transdev.de/transdev/uploads/nwb/{m.group(0)}"


# ---------------- TIME EXTRACTION ----------------
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
        return (
            datetime.fromisoformat(matches[0]),
            datetime.fromisoformat(matches[1])
        )
    except Exception:
        return None, None


# ---------------- LINE EXTRACTION ----------------
def extract_line(text: str):
    if not text:
        return "UNKNOWN"

    t = text.upper()
    t = re.sub(r"https?://\S+", " ", t)

    match = re.search(r"\b(RS|RB|RE)\s?-?\s?\d+\b", t)
    if not match:
        return "UNKNOWN"

    return match.group(0).replace(" ", "").replace("-", "")


# ---------------- DESCRIPTION CLEANING ----------------
def extract_description(text: str):
    if not text:
        return ""

    cut_markers = ["PDF-DOKUMENT", "DOWNLOAD", "HERUNTERLADEN"]

    for m in cut_markers:
        idx = text.upper().find(m)
        if idx != -1:
            text = text[:idx]

    return text.strip()


# =========================================================
# STEP 1–4 FIXED FETCH PIPELINE
# =========================================================

def fetch():
    log.info(f"Requesting {URL}")

    raw = requests.get(URL, timeout=30).text
    raw = raw.replace("\\u002F", "/").replace("\\r\\n", "\n")

    pattern = re.compile(r"https://[^\"'\s]+?\.pdf")

    seen = set()
    items = []

    for m in pattern.finditer(raw):
        pdf = normalize_pdf_url(m.group(0))

        if pdf in seen:
            continue
        seen.add(pdf)

        items.append({
            "pdf": pdf,
            "anchor": m.start()
        })

    log.info(f"PDF matches: {len(items)}")
    return items, raw


# =========================================================
# STEP 4: BUILD CALENDAR (single raw source)
# =========================================================

def build_calendar(items, raw):
    cal = Calendar()
    cal.add("prodid", "-//NWB Baustellen//")
    cal.add("version", "2.0")

    for item in items:
        pdf = item["pdf"]

        start, end = extract_times(raw)
        if not start or not end:
            continue

        line = extract_line(raw)
        desc = extract_description(raw)

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
    items, raw = fetch()
    cal = build_calendar(items, raw)
    save_calendar(cal)
    log.info("Done")


if __name__ == "__main__":
    main()
