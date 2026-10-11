#!/usr/bin/env python3
"""Überwacht Kleinanzeigen auf USM Haller Möbel im Format 3x2
(3 Felder breit, 2 Felder hoch) im Umkreis von 30 km um 60318 Frankfurt.

Pro Lauf werden die neuesten Anzeigen zu "usm haller" gelesen. Für jede neue Anzeige,
die nach Möbelstück aussieht, wird die Detailseite geladen und Titel + Beschreibung auf
3x2 geprüft. Bei einem Treffer (oder einer Preissenkung eines Treffers):
  1. Push aufs Handy über ntfy.sh (mit Foto, Tippen öffnet die Anzeige)
  2. lokal zusätzlich macOS-Mitteilung

Läuft lokal per launchd (de.luca.usm-kleinanzeigen.plist) und in der Cloud
per GitHub Actions (.github/workflows/waechter.yml).
"""
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "https://www.kleinanzeigen.de"
# l4303 = Orts-ID von 60318 Nordend, r30 = 30 km Umkreis
SEARCH = BASE + "/s-60318/{seite}usm-haller/k0l4303r30"
PAGES = 3  # neueste Anzeigen pro Lauf; mit --bestand einmalig alle Seiten

HERE = Path(__file__).resolve().parent
IN_CLOUD = os.environ.get("GITHUB_ACTIONS") == "true"
_topic_file = HERE / "ntfy_topic.txt"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC") or (_topic_file.read_text().strip() if _topic_file.exists() else "")
# Cloud und Mac führen getrennte Dateien, damit sie sich beim Git-Sync nicht stören
_suffix = "" if IN_CLOUD else "-lokal"
STATE_FILE = HERE / f"gesehen{_suffix}.json"
HITS_FILE = HERE / f"treffer{_suffix}.csv"
HITS_HEADER = "gefunden_um;id;preis;ort;titel;url\n"
LOG_FILE = HERE / "waechter.log"
BERLIN = ZoneInfo("Europe/Berlin")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"

# Nur für diese Titel lohnt die Detailseite (keine Tische, Container, Einzelteile ...)
MOEBEL = re.compile(r"sideboard|regal|kommode|highboard|lowboard|schrank|board|möbel|moebel|3\s*[x×*]\s*2", re.I)
# 3x2, 3 x 2, 3*2, 3×2 – aber nicht 3x25 oder 13x2
FORMAT = re.compile(r"(?<![\d,.])3\s*[x×*]\s*2(?![\d,.])", re.I)
BREIT_HOCH = re.compile(r"3\s*(?:felder|elemente|module|fächer|faecher)?\s*breit\D{0,25}2\s*(?:felder|elemente|module|fächer|faecher)?\s*hoch", re.I)


def log(msg):
    line = f"{datetime.now(BERLIN):%Y-%m-%d %H:%M:%S}  {msg}"
    print(line)
    with LOG_FILE.open("a") as f:
        f.write(line + "\n")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "replace")


def text_of(fragment):
    t = re.sub(r"<script.*?</script>|<svg.*?</svg>", "", fragment, flags=re.S)
    t = html.unescape(re.sub(r"<[^>]+>", "|", t))
    return [p.strip() for p in re.split(r"\|+", t) if p.strip()]


def parse_search(page):
    ads = []
    for art in page.split("<article ")[1:]:
        art = art.split("</article>")[0]
        m_id = re.search(r'data-adid="(\d+)"', art)
        m_href = re.search(r'data-href="([^"]+)"', art)
        if not (m_id and m_href):
            continue
        meta = re.search(r'<script type="application/ld\+json">(.*?)</script>', art, re.S)
        meta = json.loads(meta.group(1)) if meta else {}
        parts = text_of(art)
        # Preis steht im fett gesetzten Absatz; ein durchgestrichener Altpreis folgt mit "line-through"
        m_preis = re.search(r'<p class="[^"]*text-title3 font-strong[^"]*">([^<]*)', art)
        preis = " ".join(html.unescape(m_preis.group(1)).split()) if m_preis else ""
        ort = next((p for p in parts if re.match(r"\d{5} ", p)), "")
        km = next((p.strip("()") for p in parts if re.fullmatch(r"\(\d+ km\)", p)), "")
        ads.append({
            "id": m_id.group(1),
            "url": BASE + m_href.group(1),
            "titel": meta.get("title") or (parts[0] if parts else ""),
            "text": meta.get("description", ""),
            "bild": (meta.get("contentUrl") or "").replace("$_59.AUTO", "$_57.AUTO"),
            "preis": preis,
            "ort": f"{ort} ({km})" if km else ort,
        })
    return ads


def description(url):
    page = get(url)
    m = re.search(r'id="viewad-description-text"[^>]*>(.*?)</p>', page, re.S)
    if not m:
        return ""
    return html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<br\s*/?>", "\n", m.group(1))))


def is_3x2(text):
    # Maße allein reichen nicht: 3 x 50 cm und 2 x 75 cm sind beide ca. 152 cm breit
    return bool(FORMAT.search(text) or BREIT_HOCH.search(text))


def preis_wert(preis):
    m = re.search(r"[\d.]+", preis.replace(".", ""))
    return int(m.group()) if m else None


def notify_mac(title, text):
    if IN_CLOUD:
        return
    script = f'display notification {json.dumps(text, ensure_ascii=False)} with title {json.dumps(title, ensure_ascii=False)} sound name "Glass"'
    subprocess.run(["osascript", "-e", script], check=False)


def notify_phone(title, text, url, bild="", priority=4):
    if not NTFY_TOPIC:
        return
    msg = {"topic": NTFY_TOPIC, "title": title, "message": text, "priority": priority,
           "tags": ["mag"]}
    if url:
        msg["click"] = url
    if bild:
        msg["attach"] = bild
    req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(msg).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception as e:
        log(f"ntfy-Fehler: {e}")


def melden(ad, grund=""):
    title = f"USM 3x2: {ad['preis'] or 'Preis ?'} – {ad['ort']}"
    text = (grund + "\n" if grund else "") + ad["titel"]
    notify_mac(title, text)
    notify_phone(title, text, ad["url"], ad["bild"])
    if not HITS_FILE.exists():
        HITS_FILE.write_text(HITS_HEADER)
    with HITS_FILE.open("a") as f:
        felder = [f"{datetime.now(BERLIN):%Y-%m-%d %H:%M}", ad["id"], ad["preis"], ad["ort"],
                  ad["titel"].replace(";", ","), ad["url"]]
        f.write(";".join(felder) + "\n")
    log(f"TREFFER {ad['id']} {ad['preis']} {ad['ort']} {ad['titel']} {grund}")


def main():
    if "--test" in sys.argv or os.environ.get("WAECHTER_TEST") == "1":
        notify_phone("USM 3x2: Probealarm", "Der Kleinanzeigen-Wächter funktioniert.", SEARCH.format(seite=""))
        notify_mac("USM 3x2: Probealarm", "Der Kleinanzeigen-Wächter funktioniert.")
        return

    erster_lauf = not STATE_FILE.exists()
    seen = {} if erster_lauf else json.loads(STATE_FILE.read_text())

    ads, ids = [], set()
    for n in range(1, (50 if "--bestand" in sys.argv else PAGES) + 1):
        seite = [a for a in parse_search(get(SEARCH.format(seite="" if n == 1 else f"seite:{n}/")))
                 if a["id"] not in ids]
        if not seite:
            break
        ids.update(a["id"] for a in seite)
        ads += seite
        time.sleep(1.5)
    if not ads:
        # Kein einziges Ergebnis heißt fast immer: Seite blockiert oder Layout geändert
        log("Fehler: keine Anzeigen erkannt (blockiert oder Seitenaufbau geändert?)")
        sys.exit(1)

    neu = treffer_neu = 0
    start_treffer = []
    for ad in ads:
        alt = seen.get(ad["id"])
        if alt is None:
            neu += 1
            text = f"{ad['titel']}\n{ad['text']}"
            treffer = is_3x2(text)
            if not treffer and MOEBEL.search(ad["titel"]):
                try:
                    treffer = is_3x2(ad["titel"] + "\n" + description(ad["url"]))
                except Exception as e:
                    log(f"Detailseite {ad['id']} nicht lesbar: {e}")
                    continue  # beim nächsten Lauf erneut versuchen
                time.sleep(1.5)
            seen[ad["id"]] = {"titel": ad["titel"], "preis": ad["preis"], "treffer": treffer,
                              "zuerst": f"{datetime.now(BERLIN):%Y-%m-%d %H:%M}"}
            if treffer:
                treffer_neu += 1
                if erster_lauf:
                    start_treffer.append(ad)
                else:
                    melden(ad, "Neue Anzeige")
        elif alt.get("treffer") and ad["preis"] != alt.get("preis"):
            vorher, jetzt = preis_wert(alt.get("preis", "")), preis_wert(ad["preis"])
            if vorher and jetzt and jetzt < vorher:
                melden(ad, f"Preis gesenkt: {alt['preis']} → {ad['preis']}")
            alt["preis"] = ad["preis"]

    if erster_lauf:
        liste = "\n".join(f"• {a['preis']} – {a['ort']}: {a['titel']}" for a in start_treffer) or "Aktuell keine."
        notify_phone("USM 3x2: Überwachung aktiv",
                     f"{len(ads)} Anzeigen gelesen, {len(start_treffer)} aktuelle 3x2-Treffer:\n{liste}",
                     SEARCH.format(seite=""), priority=3)
        for a in start_treffer:
            log(f"BESTAND {a['id']} {a['preis']} {a['ort']} {a['titel']} {a['url']}")

    STATE_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=1))
    log(f"{len(ads)} Anzeigen, {neu} neu, {treffer_neu} neue 3x2-Treffer")


if __name__ == "__main__":
    main()
