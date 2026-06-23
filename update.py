from bs4 import BeautifulSoup
import requests
import json
from pathlib import Path
from datetime import datetime, timedelta
from icalendar import Calendar, Event
import re
URL = "https://www.nordwestbahn.de/de/regio-s-bahn/service/geplante-baumassnahmen"

ICS_FILE = "baustellen.ics"
KNOWN_FILE = "known.json"


def load_known():
    if Path(KNOWN_FILE).exists():
        return json.loads(Path(KNOWN_FILE).read_text())
    return []


def save_known(data):
    Path(KNOWN_FILE).write_text(json.dumps(data, indent=2))


def load_calendar():

    if Path(ICS_FILE).exists():
        from icalendar import Calendar
        return Calendar.from_ical(Path(ICS_FILE).read_bytes())

    cal = Calendar()
    cal.add('prodid', '-//NWB Baustellen//')
    cal.add('version', '2.0')
    return cal


def save_calendar(cal):
    Path(ICS_FILE).write_bytes(cal.to_ical())

def fetch():
    r = requests.get(URL)
    raw = r.text

    # extract ANY pdf URL, even partially escaped
    matches = re.findall(r"https:\\u002F\\u002F[^\"']+?\\.pdf", raw)

    results = []
    seen = set()

    for m in matches:
        url = m.replace("\\u002F", "/")

        if url not in seen:
            seen.add(url)
            results.append({"pdf": url})

    return results

known = load_known()
cal = load_calendar()

for item in fetch():

    uid = item["pdf"]

    if uid in known:
        continue

    event = Event()

    event.add('summary', item['title'])

    start = datetime.now()
    end = start + timedelta(days=1)

    event.add('dtstart', start)
    event.add('dtend', end)

    event.add(
        'description',
        f"Ersatzfahrplan:\n{item['pdf']}"
    )

    event.add('uid', uid)

    cal.add_component(event)

    known.append(uid)

save_calendar(cal)
save_known(known)
