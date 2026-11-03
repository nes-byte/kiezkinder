# Kiezkinder Kalender

Wochenübersicht mit Terminen für Kinder von 0 bis 6 Jahren in Prenzlauer Berg, Mitte und Friedrichshain.

- **Seite für die Eltern:** `index.html`
- **Pflege-Seite (nur für dich):** `admin.html`. Dort trägst du Wochenpläne, Einzeltermine und Orte mit Öffnungszeiten ein.
- **Daten:**
  - `data/manual.json`: Orte, Öffnungszeiten, Wochenpläne und Einzeltermine. Wird über die Pflege-Seite bearbeitet.
  - `data/events.json`: Termine aus den Online-Kalendern. Wird automatisch geschrieben.
  - `data/venues.json`: Zwischenspeicher für Adressen und Kartenpunkte der Orte aus berlinmitkind.de. Wird automatisch geschrieben.
- **Automatischer Abruf:** `.github/workflows/update.yml` startet `scripts/fetch_events.py` am 1. und 15. jedes Monats. Von Hand geht es über den Tab „Online-Kalender“ der Pflege-Seite oder über „Actions → Termine abholen → Run workflow“.

## Automatisch abgeholte Quellen

| Quelle | Was | Gebiet |
|---|---|---|
| Stadtbibliothek Pankow (berlin.de) | Kinder-Termine von Wasserturm, Tucholsky und Böll | diese 3 Bibliotheken |
| KREATIVHAUS (Fischerinsel) | Kategorie „Familie & Kinder“ | Fischerinsel |
| Familienzentrum Weißensee | Kurskalender ohne reine Beratungs- und Erwachsenenangebote | Mahlerstraße 4 |
| berlinmitkind.de (HIMBEER Verlag) | Veranstaltungskalender, Altersgruppen Baby und Kita | nur Orte mit PLZ in Prenzlauer Berg, Mitte-Ost und Friedrichshain |

Von berlinmitkind.de werden nur Datum, Uhrzeit, Titel, Ort und Altersangabe übernommen, keine Beschreibungstexte. Jeder Termin verlinkt zurück auf die Originalseite. Termine von Orten, die wir direkt abholen, zum Beispiel den Bibliotheken, werden dort übersprungen, damit nichts doppelt erscheint.

Kartenpunkte kommen von OpenStreetMap (Nominatim), höchstens eine Anfrage pro Sekunde, mit Zwischenspeicher.

**Nicht dabei:** kindaling.de. Die Nutzungsbedingungen verbieten, Inhalte zu sichern oder zu vervielfältigen. kindaling eignet sich aber gut zum Finden neuer nicht-kommerzieller Orte, zum Beispiel Familienzentren. Deren eigene Programme trägst du dann in der Pflege-Seite ein.

Das Skript hält sich an `robots.txt` und an die Wartezeiten der Seiten.
