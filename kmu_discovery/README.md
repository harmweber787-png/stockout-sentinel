# Swiss KMU Problem-Discovery-Engine

Findet Schweizer KMU, die nachweislich unter manueller Administration und
Medienbrüchen leiden – und trennt bezahlbaren, erreichbaren, haftungsarmen
Schmerz von blossem Schmerz.

Dieses Paket ist unabhängig vom Stockout-Sentinel-Service unter `src/`.

## Stand: Modul 2 – LINDAS-Client auf live erhobener Feldtabelle

| Baustein | Ort | Status |
|---|---|---|
| Datenmodelle (`CompanyProfile`, `Evidence`, `GateResult`, …) | `models.py` | fertig |
| Textfaltung und Begriffssuche | `gates/text.py` | fertig |
| Haftungs-K.o.-Filter | `gates/liability.py`, `config/liability_rules.yaml` | fertig |
| ERP-Negativfilter | `gates/erp.py`, `config/erp_rules.yaml` | fertig |
| Gate-Kette | `gates/base.py` | fertig |
| Lauf-Statistik (`RunStats`) | `output/stats.py` | fertig |
| LINDAS-Felderhebung (Messinstrument) | `scripts/probe_lindas.py`, `docs/lindas_feldtabelle.md` | fertig, live erhoben 2026-09-16 |
| Quellen-Unterbau (Token-Bucket, Retry, Cache, Fehler) | `sources/base.py`, `config/rate_limits.yaml` | fertig |
| LINDAS-Client (Zefix-Stammdaten) | `sources/lindas.py` | fertig |
| Weitere Quellen-Clients (Zefix-REST, SHAB, UID-BFS, Websites) | `sources/` | offen |
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
Website-Nennung nur ein Verdacht – der Betrieb bleibt drin und trägt
`erp_review_needed`. Positive Schmerzsignale (Excel, MS-Office als einzige
Anforderung, Freemail als Firmenadresse) schliessen nie aus, sondern speisen
das Rohsignal `absence_signal` für den späteren Teilscore.

## Fehlerasymmetrie: pro Gate verschieden

Die beiden Gates haben **gegenläufige** teure Fehler. Das steht als
`sensitivity`-Block in der jeweiligen YAML und ist als Modell validiert – ein
Profil, das seinen eigenen Stellschrauben widerspricht, wird beim Laden
abgelehnt.

| | Haftungs-Gate | ERP-Gate |
|---|---|---|
| Profil | `aggressive` | `conservative` |
| Teurer Fehler | verpasster Ausschluss – ein K.o.-Betrieb rutscht durch und man baut ein Produkt, das man nicht bauen darf | Fehlalarm – ein fälschlich ausgeschlossener Betrieb fällt nie auf, weil er nie mehr auftaucht |
| Im Zweifel | `REJECT`, mindestens `liability_review_needed` | `PASS` mit `erp_review_needed` |
| Stellschrauben | `below_threshold_outcome`, `thin_evidence_outcome`, `vetoed_hit_outcome` | `suspected_outcome` |

Konkret aggressiv beim Haftungs-Gate:

* Schwache Begriffe **unter** der Schwelle sind `REVIEW`, nicht folgenlos.
* Weder NOGA-Code noch Text vorhanden heisst `REVIEW` plus
  `liability_thin_evidence` – dann wurde nichts geprüft, nicht nichts gefunden.
  Eine geprüfte Website ohne NOGA gilt nicht als dünn, sonst stünde alles auf
  `REVIEW`.
* Ein entscheidender Treffer, der nur am Kontext-Veto scheiterte, geht in die
  Prüfschlange statt durch.

Preis dieser Einstellung: die `REVIEW`-Schlange wird lang, weil die schwachen
Wortlisten Alltagswörter enthalten («Behandlung», «Garantiefrist»). Das ist
gewollt – die Schraube dagegen ist `weak_hits_for_review` je Domain, nicht das
Profil.

Konkret konservativ beim ERP-Gate: mehrdeutige Anbieternamen (Sage, Klara,
Banana, Topal) dürfen nur qualifizierte Muster führen – das Modell lehnt einen
blossen Namen bei `ambiguous: true` ab. Beim Haftungs-Gate ist dieselbe Logik
enger gefasst: `Treuhand` matcht nur als exaktes Wort, und Kontext-Vetos
entlarven Zulieferer («Software für Arztpraxen» macht aus einem IT-Betrieb
keine Praxis) – ohne den Fall ungeprüft durchzulassen.

## Lauf-Statistik

Jeder Lauf endet mit einer `RunStats`-Zusammenfassung – als Tabelle in der
Konsole, als JSON unter dem Schlüssel `stats`. Sie ist die Grundlage, um
`weak_hits_for_review` und die Wortlisten an echten Zahlen zu justieren statt
am Bauchgefühl:

* Verteilung der Urteile (PASS / REVIEW / REJECT) mit Anteilen,
* `REVIEW` aufgeschlüsselt nach Grund (`liability_review:<domain>`,
  `liability_thin_evidence`, `erp_review_needed`) – das Sammelflag
  `liability_review_needed` fehlt bewusst, es wäre nur die Gesamtzahl,
* die häufigsten Wortlisten-Treffer, die zu `REVIEW` geführt haben, je mit
  Beleg.

Gezählt werden **Betriebe, nicht Treffer**: ein Begriff, der auf einer Website
zwanzigmal steht, ist ein Betrieb und kein zwanzigfaches Signal. Die Tiefe der
Trefferliste steuert `--top` (Standard 10).

```
Lauf-Statistik
==============================================================
  Betriebe geprueft : 4
  PASS              :     1  (25.0%)
  REVIEW            :     1  (25.0%)
  REJECT            :     2  (50.0%)

REVIEW nach Grund
--------------------------------------------------------------
  liability_review:gesundheit      1  (100.0%)

Haeufigste Wortlisten-Treffer mit REVIEW (Top 10)
--------------------------------------------------------------
  gesundheit/klinik                  1  Kliniken
  gesundheit/praxisgemeinschaft      1  Praxisgemeinschaft
  gesundheit/tarmed                  1  Tarmed
```

## Belege

Jeder Regeltreffer trägt Regel-ID, Feld, Originalschreibweise, Zitat mit
Kontext und – ausser bei NOGA-Treffern – die Quell-URL. Ohne Beleg kein Signal.

## Kalibrierung

Wortlisten, NOGA-Präfixe, Schwellenwerte und Veto-Begriffe stehen in
`config/*.yaml` und sind ohne Code-Änderung anpassbar. Die Startwerte sind
Marktkenntnis, keine Messung; sie gehören gegen echte Gespräche rekalibriert.

## Quellen-Unterbau

Jede Quelle bekommt genau einen Client; kein Client kennt eine andere Quelle.
Was alle brauchen, liegt in `sources/base.py` und setzt keine Kenntnis über
Felder einer Quelle voraus:

* **Token-Bucket** je Quelle, Werte in `config/rate_limits.yaml` (UID-Webservice
  hart auf 20/min, Firmen-Websites auf eine Anfrage je zwei Sekunden).
* **Retry** mit exponentiellem Backoff und Jitter; `Retry-After` hat Vorrang und
  wird gedeckelt. 429 und 5xx werden wiederholt, 4xx nie.
* **Typisierte Fehler** (`SourceUnavailableError`, `SourceTimeoutError`,
  `SourceRateLimitedError`, `SourceBadResponseError`), jeder mit Quelle und URL –
  der Client entscheidet, ob ein Betrieb übersprungen wird; der Batch läuft.
* **Rohdaten-Cache** als JSON je Anfrage mit Zeitstempel und Quell-URL.
  Wiederholte Läufe rufen nichts doppelt ab; `max_age` erzwingt Frische.
* **Zähler** für gesendete Aufrufe und Cache-Treffer je Transport.

Uhr, Schlaf und Sender sind injizierbar – die 29 Tests dazu laufen ohne Netz.

## Modul 2: LINDAS-Client

`sources/lindas.py` liest Zefix-Stammdaten über den SPARQL-Endpunkt
`https://lindas.admin.ch/query`, Graph `https://lindas.admin.ch/foj/zefix`
(793 459 Betriebe am 2026-09-16). Jeder Prädikatname stammt aus der live
erhobenen Feldtabelle in `docs/lindas_feldtabelle.md` (Rohdaten daneben als
JSON), keiner aus dem Gedächtnis.

Was der Graph liefert und wohin es geht:

| LINDAS-Feld | Abdeckung | Ziel im `CompanyProfile` |
|---|---:|---|
| `schema:legalName` | 100 % | `name`, Beleg `schema:legalName` |
| `schema:name` (Sprachfassungen) | 99.99 % | `ZefixRecord.alternate_names` |
| `schema:identifier` → `CompanyUID` | 100 % | `uid` (normalisiert auf `CHE-123.456.789`) |
| `schema:additionalType` (eCH-0097) | 100 % | `rechtsform`, Beleg |
| `schema:description` (Zweckartikel) | 98.4 % | `zweck`, `TextDocument(REGISTER_PURPOSE)`, Beleg |
| `schema:address` → Strasse, PLZ, Ort, `addressRegion` | 100 % / 99.5 % | `standort`, Beleg |
| `schema.ld.admin.ch/municipality` | 100 % | `ZefixRecord.municipality_bfs` |

Was der Graph **nicht** liefert und deshalb leer bleibt: NOGA-Code,
Grössenklasse, Website, E-Mail, zeichnungsberechtigte Personen (die Klasse
`schema:Person` hat eine einzige Instanz, die Metadaten des Datensatzes) und
ein Statusfeld. Ein Liquidationszusatz steht nur in der Firma;
`ZefixRecord.in_liquidation` erkennt ihn (de/fr/it), entscheidet aber nichts –
das ist Sache eines Gates.

```python
from pathlib import Path
from kmu_discovery.models import Rechtsform
from kmu_discovery.sources import LindasClient, profile_from_record

client = LindasClient.build(cache_dir=Path(".cache/rohdaten"))
profile = client.fetch_profile("CHE-242.294.601")          # ein Betrieb per UID
for record in client.iter_companies("GL", [Rechtsform.GMBH, Rechtsform.AG]):
    if not record.in_liquidation:
        profile = profile_from_record(record)               # Belege inklusive
```

Jede übernommene Angabe trägt einen `Evidence`-Beleg mit der dereferenzierbaren
Betriebs-IRI (`https://register.ld.admin.ch/zefix/company/<id>`) und dem
Prädikat als `locator`. Der Client kennt nur LINDAS; Rate-Limit (1 req/s,
Burst 2), Retry, Rohdaten-Cache und typisierte Fehler kommen vom Transport aus
`sources/base.py`. UID und Kanton werden vor dem Einsetzen in die Abfrage
validiert; ein Rechtsformfilter läuft über `VALUES`, nie über Textinterpolation.
Die seitenweise Abfrage sortiert stabil nach Betriebs-IRI, damit ein
wiederholter Lauf dieselben Seiten trifft und aus dem Cache bedient wird.

Live gemessen: Einzelabruf und eine Seite von 100 Betrieben antworten in unter
zwei Sekunden. Die 46 Tests des Clients laufen ohne Netz gegen Antworten in der
live gesehenen Form.

Erhebung wiederholen (der automatische Graph-Scan des Skripts zählt alle Tripel
des Endpunkts und läuft ins Timeout; Graph und Klasse deshalb explizit):

```bash
python scripts/probe_lindas.py --graph https://lindas.admin.ch/foj/zefix \
    --class https://schema.ld.admin.ch/ZefixOrganisation --timeout 120
```

## Offene Verifikationsschulden

| Punkt | Warum offen | Wie prüfen |
|---|---|---|
| UID-Prüfziffer (`uid_check_digit`) | Gewichte aus der BFS-Doku, nicht gegen einen amtlichen Testvektor geprüft | Stichprobe echter UIDs gegen den UID-Webservice des BFS; bis dahin nur beratend, das Modell prüft ausschliesslich das Format |
| ERP-Anbieterliste | aus Marktkenntnis, nicht aus gepflegter Quelle | Referenzkundenlisten der Anbieter, Stichproben echter Stelleninserate |
| NOGA-Präfixe der übrigen Haftungsfelder | aus NOGA 2008 abgeleitet | gegen die BFS-Systematik gegenlesen |

**Erledigt – keine offenen NOGA-Grenzfälle mehr.** Ausgeschlossen: 71.1
(Planer, SIA-Bezug), 16.23 sowie 25.11/25.12 (Bauschreinerei, Metall- und
Stahlbau – Werkverträge mit Mängelrüge- und Garantiefristen), 88.91 (Kitas –
Daten über Kinder) und 47.74 (Sanitätshäuser, Orthopädie – Kostengutsprachen
mit IV und Krankenkasse, also Gesundheitsdaten natürlicher Personen). Im
Kandidatenpool bleibt 75 (Veterinär): Tierdaten sind nicht besonders
schützenswert.

Damit steht derzeit kein NOGA-Präfix mehr auf `REVIEW`. Die Mechanik dafür
bleibt im Modell und getestet – sie wird gebraucht, sobald bei der Kalibrierung
ein noch nicht entschiedenes Feld auftaucht.

## Bekannte Verzerrungen

* **Grösse erzeugt Spuren.** Grössere Betriebe hinterlassen mehr Text und
  treffen mehr Regeln. Die Normalisierung auf Grössenklasse fehlt noch – sie
  gehört ins Scoring-Modul, nicht in die Gates.
* **Betriebe ohne Website sind unsichtbar.** Der ERP-Filter vermerkt das als
  `no_signal` samt Hinweis auf die Dunkelziffer, statt sie zu verschweigen.
* **Stelleninserate sind ein Lag-Indikator.** Sie zeigen den Schmerz von
  gestern, nicht den von heute.

## Entwicklung

Die vier anonymisierten Beispielseiten liegen in `kmu_discovery/examples/` und
werden sowohl vom Demo-Aufruf als auch von den Tests benutzt.

```bash
python -m pytest tests_kmu_discovery -q
python -m mypy --config-file pyproject.toml
ruff check kmu_discovery tests_kmu_discovery
```
