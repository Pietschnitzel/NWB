from pathlib import Path
from datetime import datetime
import json
import re
import logging
import requests
from icalendar import Calendar, Event
from zoneinfo import ZoneInfo
from collections import defaultdict


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
    url = url.replace("s3.storage.planetary-networks.de", "download.transdev.de")
    url = requests.utils.unquote(url)
    url = url.replace(" ", "")
    return url


# ---------------- TIME EXTRACTION ----------------
ISO_RE = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}')


def extract_times(raw: str):
    times = ISO_RE.findall(raw)

    if len(times) < 2:
        return None, None

    return (
        datetime.fromisoformat(times[0]),
        datetime.fromisoformat(times[1])
    )


# ---------------- LINE EXTRACTION ----------------
LINE_RE = re.compile(r'\b(RS|RB)\s?\d+\b')

def extract_line(text: str):
    matches = LINE_RE.findall(text)

    if not matches:
        return "UNKNOWN"

    # rebuild full tokens like RS1, RB58
    full = re.findall(r'\b(RS|RB)\s?\d+\b', text)

    if not full:
        return "UNKNOWN"

    # pick FIRST valid occurrence only (important!)
    m = re.search(r'\b(RS|RB)\s?\d+\b', text)

    if not m:
        return "UNKNOWN"

    return m.group(0).replace(" ", "")


# ---------------- FETCH ----------------
def fetch():
    log.info(f"Requesting {URL}")

    r = requests.get(URL, timeout=30)
    raw = r.text

    matches = re.findall(r"https:\\?u002F\\?u002F[^\"'\s]+?\.pdf", raw)

    log.info(f"PDF matches: {len(matches)}")

    items = []

    for m in matches:
        url = normalize_pdf_url(m)

        # extract a *local context window around this URL*
        # so line parsing is per-item, not global
        idx = raw.find(m)
        snippet = raw[max(0, idx - 800): idx + 800]

        items.append({
            "pdf": url,
            "text": snippet   # ✅ per-item context
        })

    return items


# ---------------- BUILD ICS ----------------
def build_calendar(items):
    cal = Calendar()
    cal.add("prodid", "-//NWB Baustellen//")
    cal.add("version", "2.0")

    groups = defaultdict(list)

    # ---------- GROUP ----------
    for item in items:
        pdf = item["pdf"]
        text = item["text"]   # ✅ FIX HERE

        line = extract_line(text)
        start, end = extract_times(text)

        if not start or not end:
            log.warning(f"Skipping (no time): {pdf}")
            continue

        groups[line].append((start, end, pdf, text))

    # ---------- SORT + BUILD ----------
    for line in sorted(groups.keys()):
        log.info(f"Line {line}: {len(groups[line])} events")

        for start, end, pdf, text in sorted(groups[line], key=lambda x: x[0]):

            event = Event()
            event.add("summary", f"{line} – Baustelle")

            event.add("dtstart", start)
            event.add("dtend", end)
            event.add("uid", pdf)

            event.add(
                "description",
                f"{line} Ersatzfahrplan:\n{pdf}"
            )

            event.add("categories", [line])

            cal.add_component(event)

    return cal

# ---------------- SAVE ----------------
def save_calendar(cal):
    Path(ICS_FILE).write_bytes(cal.to_ical())
    log.info("ICS written")


# ---------------- MAIN ----------------
items = fetch()

cal = build_calendar(items)

save_calendar(cal)

log.info("Done")
