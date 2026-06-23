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
    url = requests.utils.unquote(url)

    # extract file path AFTER schedule ID
    m = re.search(r'schedule/\d+/.+$', url)
    if not m:
        return url  # fallback

    path = m.group(0)

    # rebuild clean canonical URL
    return f"https://download.transdev.de/transdev/uploads/nwb/{path}"

# ---------------- TIME EXTRACTION ----------------
ISO_RE = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}')

def extract_line(text: str):
    if not text:
        return "UNKNOWN"

    # normalize separators (important!)
    t = text.upper()

    # remove URL noise influence
    t = re.sub(r'https?://\S+', ' ', t)

    # strict RS/RB patterns only
    match = re.search(r'\b(RS|RB|RE)\s?-?\s?\d+\b', t)

    if not match:
        return "UNKNOWN"

    line = match.group(0)

    # normalize formats:
    line = line.replace(" ", "").replace("-", "")

    return line
# ---------description regex------------
PDF_RE = re.compile(r'https?://\S+\.pdf|PDF-Dokument', re.IGNORECASE)

def extract_description(text: str) -> str:
    if not text:
        return ""

    # cut everything starting from PDF section
    text = PDF_RE.split(text, maxsplit=1)[0]

    return text.strip()
# ---------------- FETCH ----------------
def fetch():
    log.info(f"Requesting {URL}")

    raw = requests.get(URL, timeout=30).text
    raw = raw.replace("\\u002F", "/").replace("\\r\\n", "\n")

    pattern = re.compile(r"https://[^\"'\s]+?\.pdf")

    items = []

    for match in pattern.finditer(raw):
        pdf = normalize_pdf_url(match.group(0))
    
        start = max(0, match.start() - 800)
        end = min(len(raw), match.end() + 800)
    
        local_context = raw[start:end]
    
        start_time, end_time = extract_times(local_context)
        desc = extract_description(local_context)
    
        items.append({
            "pdf": pdf,
            "text": local_context
        })

    log.info(f"PDF matches: {len(items)}")
    return items

# -------- isolate time ------------
ISO_RE = re.compile(
    r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}'
)

def extract_times(text: str):
    if not text:
        return None, None

    times = ISO_RE.findall(text)

    if len(times) < 2:
        return None, None

    try:
        return (
            datetime.fromisoformat(times[0]),
            datetime.fromisoformat(times[1])
        )
    except Exception:
        return None, None
# ---------------- BUILD ICS ----------------
from collections import defaultdict
from icalendar import Calendar, Event


def build_calendars(items):
    calendars = defaultdict(Calendar)

    # 1. FIRST: group items by line
    grouped = defaultdict(list)

    for item in items:
        text = item["text"]
        line = extract_line(text)
        start, end = extract_times(text)
        description = extract_description(text) # i could be a problem :3
        if not start or not end:
            continue

        grouped[line].append((item["pdf"], text, start, end))

    # 2. SECOND: build calendars per line
    for line, entries in grouped.items():

        cal = Calendar()
        cal.add("prodid", f"-//NWB {line}//")
        cal.add("version", "2.0")

        for pdf, text, start, end in entries:

            event = Event()
            event.add("summary", f"{line} – Baustelle")
            event.add("dtstart", start)
            event.add("dtend", end)
            event.add("uid", pdf)
            clean_desc = extract_description(text)
            event.add(
                "description",
                f"{clean_desc}\n\nErsatzfahrplan:\n{pdf}"
            )

            cal.add_component(event)

        calendars[line] = cal

    return calendars

# ---------------- SAVE ----------------
def save_calendars(calendars):
    out_dir = Path("feeds")
    out_dir.mkdir(exist_ok=True)

    for line, cal in calendars.items():
        safe_line = line.lower().replace(" ", "")

        path = out_dir / f"{safe_line}.ics"
        path.write_bytes(cal.to_ical())

        log.info(f"Wrote {path}")


# ---------------- MAIN ----------------
items = fetch()

cal = build_calendars(items)

save_calendars(cal)

log.info("Done")
