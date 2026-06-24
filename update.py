import re
import requests
import urllib.parse
from collections import defaultdict
from datetime import datetime
import logging
import sys

URL = "https://www.nordwestbahn.de/de/service/deine-reiseplanung/meldungen"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

log = logging.getLogger("nwb-parser")


# ------------------------------------------------------------
# 1. FETCH
# ------------------------------------------------------------
def fetch(url: str) -> str:
    log.info(f"Fetching URL: {url}")

    r = requests.get(url, timeout=30)

    log.info(f"HTTP Status: {r.status_code}")
    log.info(f"Response size: {len(r.text)} chars")

    r.raise_for_status()

    return r.text


# ------------------------------------------------------------
# 2. PDF URL NORMALIZATION
# ------------------------------------------------------------
def normalize_pdf_url(url: str) -> str:
    if not url:
        return None

    url = url.replace("\\u002F", "/")
    url = urllib.parse.unquote(url)

    # Fix escaped slashes
    url = url.replace("\\/", "/")

    # Extract only PDF portion
    match = re.search(r"https?://[^\\s\"'<>]+?\\.pdf", url)
    if not match:
        return None

    url = match.group(0)

    # canonical fix: s3 -> download.transdev mapping
    if "s3.storage.planetary-networks.de" in url:
        path = re.sub(
            r"https?://s3\\.storage\\.planetary-networks\\.de/transdev/uploads/",
            "",
            url
        )

        # remove duplicate segments
        path = re.sub(r"(nwb/)+", "nwb/", path)

        url = "https://download.transdev.de/transdev/uploads/nwb/" + path

    # final cleanup: remove duplicate slashes
    url = re.sub(r"(?<!:)//+", "/", url)
    url = url.replace("https:/", "https://")

    return url


# ------------------------------------------------------------
# 3. LINE EXTRACTION
# ------------------------------------------------------------
LINE_REGEX = re.compile(r"\b(RS\s?\d+|RB\s?\d+|RE\s?\d+)\b", re.IGNORECASE)

def extract_line(text: str) -> str:
    m = LINE_REGEX.search(text)
    if not m:
        return "UNKNOWN"
    return m.group(1).replace(" ", "").upper()


# ------------------------------------------------------------
# 4. TIME EXTRACTION
# ------------------------------------------------------------
TIME_REGEX = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+\-]\d{2}:\d{2}"
)

def extract_times(text: str):
    matches = TIME_REGEX.findall(text)
    if len(matches) < 2:
        return None, None
    return matches[0], matches[1]


# ------------------------------------------------------------
# 5. DESCRIPTION EXTRACTION
# ------------------------------------------------------------
def extract_description(window: str) -> str:
    # focus on German sentence blocks
    start_markers = ["Aufgrund", "Betroffen"]
    stop_markers = ["Alle Fahrplanänderungen", ".pdf", "[bahn.de"]

    lines = window.splitlines()
    collected = []
    capture = False

    for line in lines:
        if any(m in line for m in start_markers):
            capture = True

        if capture:
            if any(m in line for m in stop_markers):
                break

            # strip URLs
            line = re.sub(r"https?://\\S+", "", line)
            line = re.sub(r"\\s+", " ", line).strip()

            if line:
                collected.append(line)

    return " ".join(collected).strip()


# ------------------------------------------------------------
# 6. CONTEXT WINDOW EXTRACTION
# ------------------------------------------------------------
def get_context(text: str, match_start: int, match_end: int, size: int = 1000):
    start = max(0, match_start - size)
    end = min(len(text), match_end + size)
    return text[start:end]


# ------------------------------------------------------------
# 7. PARSE ITEMS
# ------------------------------------------------------------
def parse_items(raw: str):
    items = []

    pdf_pattern = re.compile(r"https?\\\\?:?\\\\?/[^\\s\"']+?\\.pdf")
    matches = list(pdf_pattern.finditer(raw))

    log.info(f"PDF candidates found: {len(matches)}")

    for i, m in enumerate(matches):
        pdf_raw = m.group(0)
        log.info(f"[{i+1}/{len(matches)}] raw PDF: {pdf_raw}")

        pdf_url = normalize_pdf_url(pdf_raw)

        if not pdf_url:
            log.warning("Skipped: invalid PDF after normalization")
            continue

        context = get_context(raw, m.start(), m.end())

        start, end = extract_times(context)
        if not start or not end:
            log.warning(f"Skipped (no valid timestamps): {pdf_url}")
            continue

        line = extract_line(context)
        description = extract_description(context)

        log.info(f"Parsed event → line={line}, start={start}, end={end}")

        items.append({
            "line": line,
            "start": start,
            "end": end,
            "pdf": pdf_url,
            "description": description
        })

    # dedupe
    before = len(items)
    deduped = {i["pdf"]: i for i in items}
    after = len(deduped)

    log.info(f"Deduped events: {before} → {after}")

    return list(deduped.values())


# ------------------------------------------------------------
# 8. ICS BUILDING
# ------------------------------------------------------------
def to_ics_datetime(dt: str) -> str:
    return dt.replace(":", "").replace("-", "").split("+")[0]


def build_calendars(items):
    grouped = defaultdict(list)

    for item in items:
        grouped[item["line"]].append(item)

    log.info(f"Lines found: {len(grouped)}")

    calendars = {}

    for line, events in grouped.items():
        log.info(f"Building ICS for line {line}: {len(events)} events")

        ics = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Nordwestbahn Parser//DE"
        ]

        for e in events:
            uid = e["pdf"]

            ics.extend([
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"SUMMARY:{line} – Baustelle",
                f"DTSTART:{to_ics_datetime(e['start'])}",
                f"DTEND:{to_ics_datetime(e['end'])}",
                f"DESCRIPTION:{e['description']}\n\nErsatzfahrplan:\n{e['pdf']}",
                f"CATEGORIES:{line}",
                "END:VEVENT"
            ])

        ics.append("END:VCALENDAR")

        key = line.lower().replace(" ", "")
        calendars[key] = "\n".join(ics)

        log.info(f"Finished ICS: {key}")

    return calendars


# ------------------------------------------------------------
# 9. SAVE OUTPUT
# ------------------------------------------------------------
def save_calendars(calendars: dict):
    import os
    os.makedirs("feeds", exist_ok=True)

    log.info(f"Writing {len(calendars)} ICS files...")

    for line, ics in calendars.items():
        path = f"feeds/{line}.ics"

        with open(path, "w", encoding="utf-8") as f:
            f.write(ics)

        log.info(f"Saved: {path} ({len(ics)} bytes)")


# ------------------------------------------------------------
# MAIN PIPELINE
# ------------------------------------------------------------
def main():
    log.info("=== Nordwestbahn parser started ===")

    raw = fetch(URL)

    log.info("Parsing items...")
    items = parse_items(raw)

    log.info(f"Total events extracted: {len(items)}")

    if not items:
        log.warning("No events found — check PDF regex or page structure!")
        return

    calendars = build_calendars(items)

    save_calendars(calendars)

    log.info("=== Done ===")


if __name__ == "__main__":
    main()
