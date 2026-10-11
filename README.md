# USM-Kleinanzeigen-Wächter

Meldet neue Kleinanzeigen für **USM Haller im Format 3x2** (3 Felder breit, 2 hoch)
im Umkreis von **30 km um 60318 Frankfurt** per Push über [ntfy](https://ntfy.sh).

- Suche: `https://www.kleinanzeigen.de/s-60318/usm-haller/k0l4303r30`
- Pro Lauf werden die 3 neuesten Ergebnisseiten gelesen; für neue Anzeigen mit
  Möbel-Titel (Sideboard, Regal, Board, Schrank …) wird die Beschreibung geprüft.
- Treffer: „3x2“, „3 x 2“, „3*2“, „3×2“ oder „3 Fächer/Felder breit, 2 hoch“.
  Maße allein zählen nicht (3 × 50 cm und 2 × 75 cm sind beide ca. 152 cm breit).
- Gemeldet wird jede neue Treffer-Anzeige und jede Preissenkung eines Treffers.
- Läuft alle 10 Minuten per GitHub Actions (Secret `NTFY_TOPIC`) und optional lokal per launchd.

```bash
python3 waechter.py            # normaler Lauf
python3 waechter.py --bestand  # einmalig alle Seiten einlesen
python3 waechter.py --test     # Probe-Push
```

`gesehen.json` merkt sich bekannte Anzeigen, `treffer.csv` protokolliert alle Meldungen.
