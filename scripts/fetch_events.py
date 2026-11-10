"""Holt Kinder-Termine aus Online-Kalendern und schreibt data/events.json.

Quellen:
  - Stadtbibliothek Pankow (Kalender auf berlin.de), gefiltert nach Ort und Kategorie "Kinder, Jugendliche"
  - KREATIVHAUS / Familienzentrum Fischerinsel, Kategorie "Familie & Kinder"
  - Familienzentrum Weißensee, Kurskalender ohne reine Beratungs- und Erwachsenenangebote
  - berlinmitkind.de (HIMBEER), nur Orte mit Postleitzahl in AREA_PLZ, nur Fakten + Link zurück

Nicht dabei: kindaling.de, weil die Nutzungsbedingungen das Kopieren von Inhalten untersagen.

Respektiert robots.txt und Crawl-Delay. Läuft per GitHub Action (siehe .github/workflows/update.yml).
"""
import json
import re
import sys
import time
import datetime as dt
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = "Playces/1.0 (privater Wochenkalender fuer eine Kita-Elterngruppe; github.com)"
DAYS_AHEAD = 24
MAX_AGE_MONTHS = 72          # Angebote, die erst ab 7 Jahren starten, fliegen raus
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "events.json"

LIBRARIES = {  # berlin.de Orts-ID -> (Kalender-ID, Index des Felds "Kinder, Jugendliche", Ort-ID in data/manual.json)
    "37541": ("409", 8, "wt"),    # Bibliothek am Wasserturm (Stadtbibliothek Pankow)
    "45133": ("409", 8, "kt"),    # Kurt-Tucholsky-Bibliothek (Stadtbibliothek Pankow)
    "41678": ("409", 8, "hb"),    # Heinrich-Böll-Bibliothek (Stadtbibliothek Pankow)
    "38489": ("327", 7, "psb"),   # Philipp-Schaeffer-Bibliothek (Stadtbibliothek Mitte)
}

session = requests.Session()
session.headers["User-Agent"] = USER_AGENT
_robots = {}
_last_hit = {}


def _robots_rules(text):
    """robots.txt mit Längster-Treffer-Regel lesen (wie Google), damit z. B.
    "Disallow: /wp-admin/" + "Allow: /wp-admin/admin-ajax.php" richtig ausgewertet wird."""
    groups, agents, rules, last_was_agent = [], [], [], False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, val = (x.strip() for x in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if not last_was_agent and (agents or rules):
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(val.lower())
            last_was_agent = True
        else:
            last_was_agent = False
            if key in ("allow", "disallow", "crawl-delay"):
                rules.append((key, val))
    if agents or rules:
        groups.append((agents, rules))
    me = USER_AGENT.split("/")[0].lower()
    own = [r for a, r in groups if any(x != "*" and x in me for x in a)]
    chosen = own or [r for a, r in groups if "*" in a]
    return [rule for rs in chosen for rule in rs]


def _match(pattern, path):
    if not pattern:
        return False
    regex = re.escape(pattern).replace(r"\*", ".*")
    if regex.endswith(r"\$"):
        regex = regex[:-2] + "$"
    return re.match(regex, path) is not None


def allowed(url):
    parts = urlparse(url)
    host = parts.scheme + "://" + parts.netloc
    if host not in _robots:
        try:
            r = session.get(host + "/robots.txt", timeout=20)
            _robots[host] = _robots_rules(r.text if r.ok else "")
        except requests.RequestException:
            _robots[host] = []
    rules = _robots[host]
    path = parts.path + ("?" + parts.query if parts.query else "")
    best, verdict = -1, True
    for key, val in rules:
        if key in ("allow", "disallow") and _match(val, path) and len(val) >= best:
            if len(val) > best or key == "allow":
                best, verdict = len(val), key == "allow"
    delay = next((float(v) for k, v in rules if k == "crawl-delay" and re.match(r"^\d+(\.\d+)?$", v)), 2)
    return verdict, delay


def get(url):
    ok, delay = allowed(url)
    if not ok:
        print(f"  robots.txt verbietet: {url}")
        return None
    host = urlparse(url).netloc
    wait = _last_hit.get(host, 0) + float(delay) - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_hit[host] = time.time()
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return r.text


# ---------- Alter, Anmeldung, Kosten aus Freitext ----------

def parse_age(text):
    t = text.replace("–", "-").replace("&#8211;", "-")
    m = re.search(r"(\d+)\s*(Woche|Monat)\w*\s*(?:-|bis|und)\s*(\d+(?:,5)?)\s*(Monat|Jahr)", t, re.I)
    if m:
        lo = 0 if m.group(2).lower().startswith("woche") else int(m.group(1))
        hi = float(m.group(3).replace(",", "."))
        return lo, int(hi * 12) if m.group(4).lower().startswith("jahr") else int(hi)
    m = re.search(r"bis\s*(\d+),5\s*Jahr", t, re.I)
    if m:
        return 0, int(m.group(1)) * 12 + 6
    m = re.search(r"(\d+)\s*(?:-|bis)\s*(\d+)\s*Monat", t, re.I)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"bis\s*(\d+)\s*Monat", t, re.I)
    if m:
        return 0, int(m.group(1))
    m = re.search(r"(\d+)\s*(?:-|bis)\s*(\d+)\s*(?:-?\s*j(?:ährig|ahr|\b|\.))", t, re.I)
    if m:
        return int(m.group(1)) * 12, int(m.group(2)) * 12
    m = re.search(r"(?:ab|über)\s*(\d+)\s*(?:J\b|J\.|Jahr)", t, re.I)
    if m:
        return int(m.group(1)) * 12, None
    m = re.search(r"unter\s*(\d+)\s*(?:J\b|J\.|Jahr)", t, re.I)
    if m:
        return 0, int(m.group(1)) * 12
    if re.search(r"\bBaby|Neugeboren|Krabbel", t, re.I):
        return 0, 24
    return None, None


def age_label(lo, hi):
    if lo is None and hi is None:
        return "Alter siehe Link"
    def fmt(m):
        return f"{m} Mon." if m < 24 and m % 12 else f"{m // 12}"
    if hi is None:
        return f"ab {fmt(lo)} Jahren" if lo >= 24 or lo % 12 == 0 else f"ab {lo} Monaten"
    if lo in (0, None):
        return f"bis {hi} Monate" if hi <= 24 else f"bis {hi // 12} Jahre"
    if hi <= 24 and not (lo % 12 == 0 and hi % 12 == 0):
        return f"{lo}–{hi} Monate"
    return f"{lo // 12}–{hi // 12} Jahre"


def signup_of(text):
    if re.search(r"ohne (Vor)?anmeldung|keine (Vor)?anmeldung|Anmeldung (ist )?nicht (nötig|erforderlich)", text, re.I):
        return "ohne Anmeldung"
    if re.search(r"(ohne|keine)\s+Anm\.", text, re.I):
        return "ohne Anmeldung"
    if re.search(r"Anm\.\s*erw", text):
        return "Anmeldung erbeten"
    if re.search(r"Anm\.\s*erf", text):
        return "Anmeldung nötig"
    if re.search(r"anmeldung", text, re.I):
        return "Anmeldung nötig"
    return ""


def cost_of(text):
    if re.search(r"Spende", text, re.I):
        return "Spende erbeten"
    if re.search(r"kostenlos|kostenfrei|Eintritt frei|gebührenfrei", text, re.I):
        return "kostenlos"
    if re.search(r"kostenpflichtig|\d+\s*(€|Euro)", text, re.I):
        return "kostenpflichtig"
    return ""


def keep(lo, hi):
    return lo is None or lo <= MAX_AGE_MONTHS


# ---------- Bibliotheken (berlin.de) ----------

SCHOOL = re.compile(r"Hausaufgaben|Nachhilfe|\bKlasse\b|Jugendliche ab|Teens?\b", re.I)

def fetch_libraries(start, stop):
    events = []
    for ort, (cal, kat, place) in LIBRARIES.items():
        print(f"Bibliothek {place} ({ort})")
        for ls in range(0, 300, 10):
            url = (f"https://www.berlin.de/land/kalender/index.php?suchmaske&c={cal}"
                   f"&date_start={start:%d.%m.%Y}&date_stop={stop:%d.%m.%Y}"
                   f"&kategorie%5B{kat}%5D=3&v_ort={ort}&ls={ls}")
            html = get(url)
            if not html:
                break
            soup = BeautifulSoup(html, "html.parser")
            links = soup.select('h3 a[href*="detail="]')
            for a in links:
                item = a.find_parent("li") or a.parent.parent
                text = " ".join(item.get_text(" ").split())
                m = re.search(r"Termin:\s*(\d{2})\.(\d{2})\.(\d{4})\s*(\d{1,2}:\d{2})?(?:\s*-\s*(\d{1,2}:\d{2}))?", text)
                if not m:
                    continue
                cats = [c.get_text(strip=True) for c in item.select('a[href*="kategorie"]')]
                detail = urljoin(url, a["href"])
                detail = re.sub(r"&ls=\d+", "", detail)
                title = " ".join(a.get_text(" ").split())
                if SCHOOL.search(title):
                    continue  # Schulkinder-Angebote
                desc = ""
                try:
                    dhtml = get(detail)
                    if dhtml:
                        dtext = " ".join(BeautifulSoup(dhtml, "html.parser").get_text(" ").split())
                        i = dtext.find(title)
                        j = dtext.find("Kategorien:", i)
                        desc = dtext[i + len(title): j if j > i else i + 900] if i >= 0 else ""
                except requests.RequestException as e:
                    print("  Detailseite fehlgeschlagen:", e)
                lo, hi = parse_age(title + " " + desc)
                if not keep(lo, hi):
                    continue
                events.append({
                    "date": f"{m.group(3)}-{m.group(2)}-{m.group(1)}",
                    "start": m.group(4) or "",
                    "end": m.group(5) or "",
                    "title": title,
                    "place": place,
                    "ageMin": lo, "ageMax": hi, "ageLabel": age_label(lo, hi),
                    "signup": signup_of(desc), "cost": cost_of(desc),
                    "highlight": "Highlight" in cats,
                    "check": bool(re.search(r"Schulferien", desc)),
                    "url": detail,
                    "source": "auto",
                })
            if len(links) < 10:
                break
    return events


# ---------- KREATIVHAUS (The Events Calendar) ----------

KH_SKIP = re.compile(r"Kleiderkammer|Beratung|Deutschkurs für Eltern|Rechtsberatung|Gymnastik", re.I)


def fetch_kreativhaus(start, stop):
    events = []
    print("KREATIVHAUS")
    for page in range(1, 15):
        url = "https://www.kreativhaus-berlin.de/veranstaltungen/kategorie/familie/"
        if page > 1:
            url += f"seite/{page}/"
        try:
            html = get(url)
        except requests.HTTPError as e:
            print("  Ende der Seiten:", e)
            break
        if not html:
            break
        soup = BeautifulSoup(html, "html.parser")
        arts = soup.select("article.tribe-events-calendar-list__event")
        if not arts:
            break
        last = None
        for art in arts:
            a = art.select_one("a.tribe-events-calendar-list__event-title-link")
            tm = art.select_one("time.tribe-events-calendar-list__event-datetime")
            if not a or not tm:
                continue
            date = tm.get("datetime", "")[:10]
            last = date
            try:
                d = dt.date.fromisoformat(date)
            except ValueError:
                continue
            if d < start or d > stop:
                continue
            title = " ".join(a.get_text(" ").split())
            if KH_SKIP.search(title):
                continue
            times = re.findall(r"\d{1,2}:\d{2}", tm.get_text(" "))
            desc_el = art.select_one(".tribe-events-calendar-list__event-description")
            desc = " ".join(desc_el.get_text(" ").split()) if desc_el else ""
            venue_el = art.select_one(".tribe-events-calendar-list__event-venue-title")
            venue = venue_el.get_text(strip=True) if venue_el else ""
            lo, hi = parse_age(title + " " + desc)
            if not keep(lo, hi):
                continue
            events.append({
                "date": date,
                "start": times[0] if times else "",
                "end": times[1] if len(times) > 1 else "",
                "title": title,
                "place": "kh",
                "venue": venue,
                "ageMin": lo, "ageMax": hi, "ageLabel": age_label(lo, hi),
                "signup": signup_of(desc), "cost": cost_of(desc),
                "highlight": "tribe-recurring-event" not in " ".join(art.get("class", [])),
                "url": a.get("href", ""),
                "source": "auto",
            })
        if last and dt.date.fromisoformat(last) > stop:
            break
    return events


# ---------- berlinmitkind.de (HIMBEER Verlag) ----------
# Nur Fakten (Datum, Uhrzeit, Titel, Ort, Alter) und ein Link zurück, keine Beschreibungstexte.

AREA_PLZ = {
    "10405", "10407", "10409", "10435", "10437", "10439",   # Prenzlauer Berg
    "10115", "10119", "10178", "10179",                     # Mitte, nah dran
    "10243", "10245", "10247", "10249",                     # Friedrichshain
}
OWN_VENUES = re.compile(r"Familienzentrum Wei(ß|ss)ensee|Philipp-Schaeffer|Wasserturm|Tucholsky|Heinrich-B[öo]ll-Bibliothek|Kreativhaus|Fischerinsel|Menschenskinder|Mamunette|Ludothek", re.I)
BMK_HIGHLIGHT = {"Fest", "Flohmarkt"}
VENUES = ROOT / "data" / "venues.json"
_venues = json.loads(VENUES.read_text(encoding="utf-8")) if VENUES.exists() else {}
_last_geo = 0.0


def slug(text):
    t = text.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:40]


def geocode(street, plz):
    """OpenStreetMap Nominatim, max. 1 Anfrage pro Sekunde, Ergebnis wird in data/venues.json gemerkt."""
    global _last_geo
    wait = _last_geo + 1.1 - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_geo = time.time()
    try:
        r = session.get("https://nominatim.openstreetmap.org/search",
                        params={"street": street, "postalcode": plz, "city": "Berlin", "country": "Germany",
                                "format": "json", "limit": 1}, timeout=30)
        hits = r.json() if r.ok else []
        if hits:
            return round(float(hits[0]["lat"]), 5), round(float(hits[0]["lon"]), 5)
    except (requests.RequestException, ValueError):
        pass
    return None, None


def venue_info(url, name):
    if url in _venues:
        return _venues[url]
    info = {"name": name, "address": "", "plz": "", "lat": None, "lng": None}
    try:
        html = get(url)
        if html:
            soup = BeautifulSoup(html, "html.parser")
            addr = soup.find("address")
            lines = [" ".join(x.split()) for x in addr.get_text("\n").split("\n")] if addr else []
            lines = [x for x in lines if x]
            street = plz = ""
            for i, line in enumerate(lines):
                m = re.match(r"(1\d{4})\s+Berlin", line)
                if m and i > 0:
                    plz, street = m.group(1), lines[i - 1]
                    break
            if plz:
                info.update(address=f"{street}, {plz} Berlin", plz=plz)
                if plz in AREA_PLZ:
                    info["lat"], info["lng"] = geocode(street, plz)
    except requests.RequestException as e:
        print("  Ort nicht lesbar:", url, e)
        return info  # nicht merken, nächstes Mal neu versuchen
    _venues[url] = info
    return info


def fetch_berlinmitkind(start, stop):
    events, places, seen = [], {}, set()
    print("berlinmitkind.de")
    for page in range(1, 60):
        url = ("https://berlinmitkind.de/veranstaltungskalender/?id=1&ajax=1&view=list"
               "&age%5B%5D=baby&age%5B%5D=kitakinder"
               f"&von={start.isoformat()}&bis={stop.isoformat()}&seite={page}")
        html = get(url)
        if not html:
            break
        soup = BeautifulSoup(html, "html.parser")
        arts = soup.select("article.em-event")
        if not arts:
            break
        past_stop = False
        for art in arts:
            a = art.select_one(".entry-title a")
            d = art.select_one(".date")
            loc = art.select_one(".location a")
            if not a or not d or not loc:
                continue
            dm = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", d.get_text(strip=True))
            if not dm:
                continue
            date = f"{dm.group(3)}-{dm.group(2)}-{dm.group(1)}"
            if date > stop.isoformat():
                past_stop = True
                continue
            if date < start.isoformat():
                continue
            venue_name = loc.get_text(" ", strip=True)
            if OWN_VENUES.search(venue_name):
                continue  # kommt schon direkt von der Einrichtung
            title = " ".join(a.get_text(" ").split())
            times = re.findall(r"\d{1,2}:\d{2}", (art.select_one(".time") or d).get_text(" "))
            key = (date, times[0] if times else "", title.lower(), venue_name.lower())
            if key in seen:
                continue
            seen.add(key)
            venue = venue_info(loc["href"], venue_name)
            if venue.get("plz") not in AREA_PLZ:
                continue
            cats = {c.strip() for c in (art.select_one(".cat").get_text() if art.select_one(".cat") else "").split(",") if c.strip()}
            info = " ".join((art.select_one(".content").get_text(" ") if art.select_one(".content") else "").split())
            age_txt = " ".join(x.get_text(" ") for x in art.select(".age")) + " " + info
            lo, hi = parse_age(title + " " + age_txt)
            if not keep(lo, hi):
                continue
            pid = "bmk-" + slug(venue_name)
            if pid not in places:
                places[pid] = {"name": venue_name, "type": "Veranstaltungsort", "address": venue["address"],
                               "url": loc["href"], "auto": True}
                if venue.get("lat"):
                    places[pid].update(lat=venue["lat"], lng=venue["lng"])
            cost = "kostenlos" if "kostenlos" in cats or re.search(r"kostenlos|Eintritt frei", info, re.I) else ""
            events.append({
                "date": date,
                "start": times[0] if times else "",
                "end": times[1] if len(times) > 1 else "",
                "title": title,
                "place": pid,
                "ageMin": lo, "ageMax": hi, "ageLabel": age_label(lo, hi),
                "signup": signup_of(info), "cost": cost,
                "highlight": bool(cats & BMK_HIGHLIGHT),
                "url": a.get("href", ""),
                "via": "berlinmitkind.de",
                "source": "auto",
            })
        if past_stop:
            break
    VENUES.write_text(json.dumps(_venues, ensure_ascii=False, indent=1), encoding="utf-8")
    return events, places


# ---------- Familienzentrum Weißensee (Kurskalender, JSON vom Plugin "Weekly Class") ----------

FZW_SKIP = re.compile(r"Beratung|Sprechstunde|Geburtstrauma|Yoga|Strick|Schwangeren|ElternRaum", re.I)


def fetch_weissensee(start, stop):
    print("Familienzentrum Weißensee")
    url = ("https://www.familienzentrum-weissensee.de/wp-admin/admin-ajax.php?action=wcs_get_events_json"
           "&content%5Bwcs_type%5D%5B%5D=27&content%5Bwcs_type%5D%5B%5D=30"
           "&content%5Bwcs_type%5D%5B%5D=28&content%5Bwcs_type%5D%5B%5D=29"
           f"&start={start.isoformat()}&end={stop.isoformat()}")
    raw = get(url)
    if not raw:
        return []
    events = []
    for e in json.loads(raw):
        title = " ".join(BeautifulSoup(e.get("title", ""), "html.parser").get_text(" ").split())
        if not title or FZW_SKIP.search(title):
            continue
        date, start_t, end_t = e["start"][:10], e["start"][11:16], (e.get("end") or "")[11:16]
        if date in str((e.get("meta") or {}).get("_wcs_canceled", "")):
            continue  # fällt an diesem Tag aus
        text = " ".join(BeautifulSoup(e.get("excerpt", ""), "html.parser").get_text(" ").split())
        lo, hi = parse_age(title + " " + text)
        if not keep(lo, hi):
            continue
        events.append({
            "date": date, "start": start_t, "end": end_t, "title": title, "place": "fzw",
            "ageMin": lo, "ageMax": hi, "ageLabel": age_label(lo, hi),
            "signup": signup_of(text), "cost": cost_of(text), "highlight": False,
            "url": e.get("permalink") or "https://www.familienzentrum-weissensee.de/kurskalender-neu/",
            "source": "auto",
        })
    return events


def main():
    today = dt.date.today()
    stop = today + dt.timedelta(days=DAYS_AHEAD)
    found, problems, places = [], [], {}
    try:
        got, places = fetch_berlinmitkind(today, stop)
        print(f"berlinmitkind.de: {len(got)} Termine an {len(places)} Orten")
        if not got:
            problems.append("berlinmitkind.de: keine Termine gefunden")
        found += got
    except Exception as e:
        print(f"berlinmitkind.de fehlgeschlagen: {e}", file=sys.stderr)
        problems.append(f"berlinmitkind.de: {e}")
    for name, fn in (("Bibliotheken", fetch_libraries), ("KREATIVHAUS", fetch_kreativhaus),
                     ("Familienzentrum Weißensee", fetch_weissensee)):
        try:
            got = fn(today, stop)
            print(f"{name}: {len(got)} Termine")
            if not got:
                problems.append(f"{name}: keine Termine gefunden")
            found += got
        except Exception as e:  # eine kaputte Quelle soll die andere nicht blockieren
            print(f"{name} fehlgeschlagen: {e}", file=sys.stderr)
            problems.append(f"{name}: {e}")

    # Bei Totalausfall die alte Datei behalten
    if not found:
        print("Keine Termine gefunden, events.json bleibt unverändert.", file=sys.stderr)
        sys.exit(1)

    found.sort(key=lambda e: (e["date"], e["start"], e["title"]))
    OUT.write_text(json.dumps({
        "updated": today.isoformat(),
        "until": stop.isoformat(),
        "problems": problems,
        "places": places,
        "events": found,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Gespeichert: {len(found)} Termine bis {stop}")


if __name__ == "__main__":
    main()
