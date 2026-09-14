# Swiss KMU Problem-Discovery-Engine

Findet Schweizer KMU, die nachweislich unter manueller Administration und
Medienbrüchen leiden – und trennt bezahlbaren, erreichbaren, haftungsarmen
Schmerz von blossem Schmerz.

Dieses Paket ist unabhängig vom Stockout-Sentinel-Service unter `src/`.

## Stand: Modul 1 – Datenmodelle und deterministische Gates

| Baustein | Ort | Status |
|---|---|---|
| Datenmodelle (`CompanyProfile`, `Evidence`, `GateResult`, …) | `models.py` | fertig |
| Textfaltung und Begriffssuche | `gates/text.py` | fertig |
| Haftungs-K.o.-Filter | `gates/liability.py`, `config/liability_rules.yaml` | fertig |
| ERP-Negativfilter | `gates/erp.py`, `config/erp_rules.yaml` | fertig |
| Gate-Kette | `gates/base.py` | fertig |
| Quellen-Clients (Zefix, LINDAS, SHAB, …) | `sources/` | offen |
| LLM-Extraktion mit Structured Outputs | `extraction/` | offen |
| Scoring und Cluster-Report | `scoring/`, `output/` | offen |

## Rechtlicher Rahmen (nicht verhandelbar)

* **revDSG** – Personendaten natürlicher Personen nur aus offiziellen Registern
  (Zefix, LINDAS, SHAB), nur zweckgebunden für die Kontaktaufnahme. Das Modell
  `Entscheider` erzwingt das per Validator. Keine besonders schützenswerten
  Daten, keine Profilbildung über Privatpersonen, keine SHAB-Rubrik SB.
* **UWG Art. 3 Abs. 1 lit. o** – Die Engine erzeugt keine automatisierten
  Werbe-E-Mails und enthält keine Versandlogik. Sie liefert Kandidatenlisten
  für manuelle Kontaktaufnahme. Das bleibt so.
* **UWG Art. 5 lit. c** – Kein AGB-widriges Scraping (jobs.ch, LinkedIn,
  Moneyhouse, …), keine Umgehung von Captchas, Logins oder Rate-Limits.
  Firmen-Websites nur robots.txt-konform.

## Benutzung

```bash
pip install -r requirements-kmu-discovery.txt

# Eingebautes Beispiel
python -m kmu_discovery --demo

# Eigene Betriebe prüfen (JSON-Liste von CompanyProfile-Objekten)
python -m kmu_discovery --input betriebe.json --format json
```

```python
from kmu_discovery import default_gates, run_gates
from kmu_discovery.models import CompanyProfile

report = run_gates(
    CompanyProfile(uid="CHE-100.000.001", name="Muster Treuhand AG"),
    default_gates(),
)
report.outcome        # GateOutcome.REJECT
report.rejected_by()  # ['liability']
report.flags          # ('liability_reject:treuhand',)
```

## Wie die Gates entscheiden

Beide Gates sind reiner Code plus YAML-Wortlisten – kein LLM-Ermessen. Das LLM
extrahiert später, es schliesst nie aus.

**Haftungs-K.o.-Filter** (Gesundheit, Recht, Bau/Handwerk, Personalverleih,
Treuhand, Finanz):

| Fundstelle | Urteil |
|---|---|
| NOGA-Code im Ausschluss-Präfix | `REJECT` |
| NOGA-Code im Grenzfall-Präfix | `REVIEW` |
| entscheidender Begriff (Name, Zweck, Website, Inserat) | `REJECT` |
| genügend schwache Begriffe im Zweck oder Namen | `REJECT` |
| genügend schwache Begriffe auf Website/Inserat | `REVIEW` |
| einzelner schwacher Begriff | folgenlos, aber protokolliert |

Grenzfälle tragen `liability_review_needed` – sie werden nie stillschweigend
durchgelassen.

**ERP-Negativfilter:** Nennung in einem Stelleninserat, auf einer Portal-Domain
oder im Zweckartikel ist ein harter Nachweis (`REJECT`); eine unqualifizierte
Website-Nennung nur ein Verdacht (`REVIEW`). Positive Schmerzsignale (Excel,
MS-Office als einzige Anforderung, Freemail als Firmenadresse) schliessen nie
aus, sondern speisen das Rohsignal `absence_signal` für den späteren Teilscore.

Zwei Fehlerklassen werden bewusst getrennt behandelt: ein verpasster Ausschluss
fällt später auf, ein **falsches** `REJECT` nie. Deshalb haben mehrdeutige
Anbieternamen (Sage, Klara, Banana, Topal) nur qualifizierte Muster – das
Modell lehnt einen blossen Namen bei `ambiguous: true` ab –, deshalb matcht
`Treuhand` nur als exaktes Wort, und deshalb gibt es Kontext-Vetos für
Zulieferer («Software für Arztpraxen» macht aus einem IT-Betrieb keine Praxis).

## Belege

Jeder Regeltreffer trägt Regel-ID, Feld, Originalschreibweise, Zitat mit
Kontext und – ausser bei NOGA-Treffern – die Quell-URL. Ohne Beleg kein Signal.

## Kalibrierung

Wortlisten, NOGA-Präfixe, Schwellenwerte und Veto-Begriffe stehen in
`config/*.yaml` und sind ohne Code-Änderung anpassbar. Die Startwerte sind
Marktkenntnis, keine Messung; sie gehören gegen echte Gespräche rekalibriert.

## Offene Verifikationsschulden

| Punkt | Warum offen | Wie prüfen |
|---|---|---|
| UID-Prüfziffer (`uid_check_digit`) | Gewichte aus der BFS-Doku, nicht gegen einen amtlichen Testvektor geprüft | Stichprobe echter UIDs gegen den UID-Webservice des BFS; bis dahin nur beratend, das Modell prüft ausschliesslich das Format |
| ERP-Anbieterliste | aus Marktkenntnis, nicht aus gepflegter Quelle | Referenzkundenlisten der Anbieter, Stichproben echter Stelleninserate |
| NOGA-Präfixe der Haftungsfelder | aus NOGA 2008 abgeleitet | gegen die BFS-Systematik gegenlesen, besonders 71.1, 16.23, 25.11/25.12, 88.91 |

## Bekannte Verzerrungen

* **Grösse erzeugt Spuren.** Grössere Betriebe hinterlassen mehr Text und
  treffen mehr Regeln. Die Normalisierung auf Grössenklasse fehlt noch – sie
  gehört ins Scoring-Modul, nicht in die Gates.
* **Betriebe ohne Website sind unsichtbar.** Der ERP-Filter vermerkt das als
  `no_signal` samt Hinweis auf die Dunkelziffer, statt sie zu verschweigen.
* **Stelleninserate sind ein Lag-Indikator.** Sie zeigen den Schmerz von
  gestern, nicht den von heute.

## Entwicklung

```bash
python -m pytest tests_kmu_discovery -q
python -m mypy --config-file pyproject.toml
ruff check kmu_discovery tests_kmu_discovery
```
