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


def preprocess(raw):
    return raw.replace("\\u002F", "/").replace("\\/", "/")


# ------------------------------------------------------------
# PDF NORMALIZATION
# ------------------------------------------------------------
def normalize_pdf_url(url: str) -> str:
    if not url:
        return url

    url = url.replace("\\u002F", "/").replace("\\/", "/")

    if "download.transdev.de" in url:
        return url

    if "s3.storage.planetary-networks.de" in url:
        m = re.search(r"/schedule/\d+/.*\.pdf", url)
        if m:
            return "https://download.transdev.de/transdev/uploads/nwb" + m.group(0)

    return url


# ------------------------------------------------------------
# TIME EXTRACTION
# ------------------------------------------------------------
TIME_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+\-]\d{2}:\d{2}"
)

def extract_times(text):
    t = TIME_RE.findall(text)
    if len(t) >= 2:
        return t[0], t[1]
    if len(t) == 1:
        return t[0], t[0]
    return None, None


# ------------------------------------------------------------
# DESCRIPTION EXTRACTION (ROBUST)
# ------------------------------------------------------------
def extract_description(block):
    # 1. structured field
    m = re.search(r'"long_description"\s*:\s*"(.*?)"', block, re.DOTALL)
    if m:
        return m.group(1)

    # 2. German fallback
    m = re.search(r"(Aufgrund.*?)(?=\n\n|https|Alle Fahrplanänderungen|$)", block, re.DOTALL)
    if m:
        return m.group(1).strip()

    return "Fahrplanabweichung im Streckennetz."


# ------------------------------------------------------------
# MAIN EXTRACTION (FIXED CORE LOGIC)
# ------------------------------------------------------------
def extract_events(raw):
    raw = preprocess(raw)

    log.info("Searching ALL incidents (no type dependency)...")

    # STEP 1: find ALL PDFs first (anchor points)
    pdfs = list(re.finditer(r"https?://[^\s\"')]+\.pdf", raw))

    log.info(f"PDF anchors found: {len(pdfs)}")

    events = []

    for p in pdfs:
        pdf = normalize_pdf_url(p.group(0))

        idx = p.start()
        window = raw[max(0, idx - 2500): idx + 2500]

        # STEP 2: line extraction (independent)
        line_match = re.search(r"\b(RS|RB|RE)\s?\d+", window)
        line = line_match.group(0).replace(" ", "").upper() if line_match else "UNKNOWN"

        # STEP 3: time extraction
        start, end = extract_times(window)
        if not start:
            log.warning(f"Skipping {line}: no time found")
            continue
        if not end:
            end = start

        # STEP 4: description
        desc = extract_description(window)

        # STEP 5: optional incident type (non-blocking)
        type_match = re.search(r"interruption-[\w-]+", window)
        incident_type = type_match.group(0) if type_match else "unknown"

        events.append({
            "type": incident_type,
            "line": line,
            "start": start,
            "end": end,
            "pdf": pdf,
            "description": desc
        })

        log.info(f"Event: {line} | {incident_type}")

    log.info(f"Total events: {len(events)}")
    return events


# ------------------------------------------------------------
# GROUP
# ------------------------------------------------------------
def group_by_line(events):
    grouped = defaultdict(list)

    for e in events:
        line = e.get("line", "UNKNOWN")

        # normalize again defensively
        line = line.replace(" ", "").upper()

        grouped[line].append(e)

    return grouped

# ------------------------------------------------------------
# ICS HELPERS
# ------------------------------------------------------------
def ics_escape(text: str) -> str:
    if not text:
        return ""
    return (
        text.replace("\\", "\\\\")
            .replace(";", r"\;")
            .replace(",", r"\,")
            .replace("\n", r"\n")
            .replace("\r", "")
    )


def to_ics(dt):
    return dt.replace(":", "").replace("-", "").split("+")[0]
    
def safe_filename(line: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "", line).lower() or "unknown"

# ------------------------------------------------------------
# BUILD ICS (FIXED)
# ------------------------------------------------------------
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

            description = f"{e['description']}\n\nErsatzfahrplan:\n{e['pdf']}"

            ics.extend([
                "BEGIN:VEVENT",
                f"UID:{e['pdf']}",
                f"SUMMARY:{line} – Baustelle",
                f"DTSTART:{to_ics(e['start'])}",
                f"DTEND:{to_ics(e['end'])}",
                f"DESCRIPTION:{ics_escape(description)}",
                f"CATEGORIES:{line}",
                "END:VEVENT"
            ])

        ics.append("END:VCALENDAR")

        filename = safe_filename(line)

        output[filename] = "\n".join(ics)

    return output


# ------------------------------------------------------------
# SAVE
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
        log.error("No events found")
        return

    grouped = group_by_line(events)

    ics_files = build_ics(grouped)

    save(ics_files)

    log.info("=== DONE ===")


if __name__ == "__main__":
    main()
