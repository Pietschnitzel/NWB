import re
import requests
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
def fetch(url):
    log.info(f"Fetching {url}")
    r = requests.get(url, timeout=30)
    log.info(f"HTTP {r.status_code} | size={len(r.text)}")
    r.raise_for_status()
    return r.text


# ------------------------------------------------------------
# CLEAN ESCAPES
# ------------------------------------------------------------
def preprocess(raw):
    raw = raw.replace("\\u002F", "/").replace("\\/", "/")
    return raw


# ------------------------------------------------------------
# LINE + TIME + PDF DETECTION
# ------------------------------------------------------------
LINE_RE = re.compile(r"\b(RS\s?\d+|RB\s?\d+|RE\s?\d+)\b", re.IGNORECASE)
TIME_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+\-]\d{2}:\d{2}"
)


def extract_line(text):
    m = LINE_RE.search(text)
    return m.group(1).replace(" ", "").upper() if m else "UNKNOWN"


def extract_times(text):
    t = TIME_RE.findall(text)
    if len(t) < 2:
        return None, None
    return t[0], t[1]


def extract_pdf(text):
    m = re.search(r"https?://[^\s\"')]+\.pdf", text)
    return m.group(0) if m else None


# ------------------------------------------------------------
# CORE PARSER (YOUR REAL DATA MODEL)
# ------------------------------------------------------------
def extract_events(raw):
    raw = preprocess(raw)

    log.info("Searching incident blocks...")

    # match full incident segments by type
    pattern = re.compile(
        r"(interruption-[\w-]+).*?"
        r"(RS\s?\d+|RB\s?\d+|RE\s?\d+).*?"
        r"(https?://[^\s\"')]+\.pdf)",
        re.DOTALL
    )

    matches = pattern.findall(raw)

    log.info(f"Matched blocks: {len(matches)}")

    events = []

    for m in matches:
        incident_type = m[0]
        line = m[1].replace(" ", "").upper()
        pdf = m[2]

        # grab surrounding context for times + description
        idx = raw.find(pdf)
        window = raw[max(0, idx-1200): idx+1200]

        start, end = extract_times(window)

        if not start or not end:
            continue

        events.append({
            "type": incident_type,
            "line": line,
            "start": start,
            "end": end,
            "pdf": pdf
        })

        log.info(f"Event: {line} | {incident_type}")

    log.info(f"Total events: {len(events)}")

    return events

# ------------------------------------------------------------
# GROUP BY LINE
# ------------------------------------------------------------
def group_by_line(events):
    grouped = defaultdict(list)
    for e in events:
        grouped[e["line"]].append(e)
    return grouped


# ------------------------------------------------------------
# ICS HELPERS
# ------------------------------------------------------------
def to_ics(dt):
    return dt.replace(":", "").replace("-", "").split("+")[0]


def build_ics(grouped):
    output = {}

    for line, events in grouped.items():

        log.info(f"Building ICS for {line}: {len(events)} events")

        ics = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//NWB//Disruptions//DE"
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

        output[line.lower().replace(" ", "")] = "\n".join(ics)

    return output


# ------------------------------------------------------------
# SAVE FILES
# ------------------------------------------------------------
def save(files):
    import os
    os.makedirs("feeds", exist_ok=True)

    for name, content in files.items():
        path = f"feeds/{name}.ics"
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        log.info(f"Saved {path}")


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------
def main():
    log.info("=== START ===")

    raw = fetch(URL)

    events = extract_events(raw)

    if not events:
        log.error("No events found — structure changed or parsing failed")
        return

    grouped = group_by_line(events)

    ics_files = build_ics(grouped)

    save(ics_files)

    log.info("=== DONE ===")


if __name__ == "__main__":
    main()
