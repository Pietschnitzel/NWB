import re
import requests
import urllib.parse
import logging
from collections import defaultdict

URL = "https://www.nordwestbahn.de/de/service/deine-reiseplanung/meldungen"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("nwb")


# ------------------------------------------------------------
# FETCH
# ------------------------------------------------------------
def fetch(url: str) -> str:
    log.info(f"Fetching: {url}")
    r = requests.get(url, timeout=30)
    log.info(f"HTTP {r.status_code} | size={len(r.text)}")
    r.raise_for_status()
    return r.text


# ------------------------------------------------------------
# PREPROCESS (CRITICAL FIX)
# ------------------------------------------------------------
def preprocess(raw: str) -> str:
    raw = raw.replace("\\u002F", "/")
    raw = raw.replace("\\/", "/")
    raw = raw.replace("\r", "\n")

    # decode unicode escapes safely
    try:
        raw = raw.encode("utf-8").decode("unicode_escape")
    except Exception:
        pass

    return raw


# ------------------------------------------------------------
# PDF NORMALIZATION
# ------------------------------------------------------------
def normalize_pdf_url(url: str) -> str:
    if not url:
        return None

    url = urllib.parse.unquote(url)

    if ".pdf" not in url:
        return None

    # canonical mapping rule
    if "s3.storage.planetary-networks.de" in url:
        path = re.sub(
            r"https?://s3\.storage\.planetary-networks\.de/transdev/uploads/",
            "",
            url
        )
        path = re.sub(r"(nwb/)+", "nwb/", path)
        url = "https://download.transdev.de/transdev/uploads/nwb/" + path

    return url


# ------------------------------------------------------------
# LINE EXTRACTION
# ------------------------------------------------------------
LINE_REGEX = re.compile(r"\b(RS\s?\d+|RB\s?\d+|RE\s?\d+)\b", re.IGNORECASE)

def extract_line(text: str) -> str:
    m = LINE_REGEX.search(text)
    return m.group(1).replace(" ", "").upper() if m else "UNKNOWN"


# ------------------------------------------------------------
# TIME EXTRACTION
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
# DESCRIPTION EXTRACTION
# ------------------------------------------------------------
def extract_description(text: str) -> str:
    start_markers = ["Aufgrund", "Betroffen"]
    stop_markers = ["download", ".pdf", "Alle Fahrplanänderungen"]

    lines = text.splitlines()
    capture = False
    out = []

    for line in lines:
        if any(s in line for s in start_markers):
            capture = True

        if capture:
            if any(s in line for s in stop_markers):
                break

            line = re.sub(r"https?://\S+", "", line).strip()
            if line:
                out.append(line)

    return " ".join(out).strip()


# ------------------------------------------------------------
# STRUCTURED EVENT EXTRACTION (MAIN FIX)
# ------------------------------------------------------------
def extract_events(raw: str):
    raw = preprocess(raw)

    log.info("Scanning structured 'download' blocks...")

    # Each event block contains "download": "<pdf>"
    pattern = re.compile(r'"download"\s*:\s*"([^"]+)"')

    events = []

    for i, m in enumerate(pattern.finditer(raw)):
        pdf_raw = m.group(1)

        pdf_url = normalize_pdf_url(pdf_raw)
        if not pdf_url:
            log.warning(f"[{i}] invalid pdf skipped")
            continue

        # take local context window around match
        start = max(0, m.start() - 1200)
        end = min(len(raw), m.end() + 1200)
        context = raw[start:end]

        start_time, end_time = extract_times(context)
        if not start_time or not end_time:
            log.warning(f"[{i}] skipped (missing timestamps)")
            continue

        line = extract_line(context)
        description = extract_description(context)

        log.info(f"[{i}] OK → {line} | {start_time}")

        events.append({
            "line": line,
            "start": start_time,
            "end": end_time,
            "pdf": pdf_url,
            "description": description
        })

    # dedupe
    deduped = {e["pdf"]: e for e in events}

    log.info(f"Events extracted: {len(events)} | deduped: {len(deduped)}")

    return list(deduped.values())


# ------------------------------------------------------------
# ICS HELPERS
# ------------------------------------------------------------
def to_ics(dt: str) -> str:
    return dt.replace(":", "").replace("-", "").split("+")[0]


def build_calendars(items):
    grouped = defaultdict(list)

    for i in items:
        grouped[i["line"]].append(i)

    log.info(f"Building calendars for {len(grouped)} lines")

    out = {}

    for line, events in grouped.items():
        log.info(f"{line}: {len(events)} events")

        ics = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//NWB Parser//DE"
        ]

        for e in events:
            ics.extend([
                "BEGIN:VEVENT",
                f"UID:{e['pdf']}",
                f"SUMMARY:{line} – Baustelle",
                f"DTSTART:{to_ics(e['start'])}",
                f"DTEND:{to_ics(e['end'])}",
                f"DESCRIPTION:{e['description']}\n\nErsatzfahrplan:\n{e['pdf']}",
                f"CATEGORIES:{line}",
                "END:VEVENT"
            ])

        ics.append("END:VCALENDAR")

        out[line.lower().replace(" ", "")] = "\n".join(ics)

    return out


# ------------------------------------------------------------
# SAVE
# ------------------------------------------------------------
def save_calendars(calendars):
    import os
    os.makedirs("feeds", exist_ok=True)

    for name, data in calendars.items():
        path = f"feeds/{name}.ics"
        with open(path, "w", encoding="utf-8") as f:
            f.write(data)
        log.info(f"Saved {path}")


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------
def main():
    log.info("=== START ===")

    raw = fetch(URL)

    events = extract_events(raw)

    if not events:
        log.error("No events extracted → check structure or encoding")
        return

    calendars = build_calendars(events)

    save_calendars(calendars)

    log.info("=== DONE ===")


if __name__ == "__main__":
    main()
