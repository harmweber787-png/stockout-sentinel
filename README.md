# Stockout-Sentinel

Generischer, produktionsreifer Dispositions-Service für B2B-Lieferketten.

Stockout-Sentinel prognostiziert je Artikel den künftigen Bedarf, berechnet
Meldebestand und Nachbestellmenge und liefert eine nach Dringlichkeit sortierte
**Prioritätenliste** zurück.

Der Service ist bewusst **offen und branchenneutral**: Er enthält keine
hinterlegten Stamm-, Beispiel- oder Sortimentsdaten. Analysiert wird
ausschließlich, was im Request übergeben wird.

---

## Eigenschaften

| Eigenschaft | Umsetzung |
|---|---|
| **Zustandslos** | Jeder Request ist unabhängig; horizontal skalierbar. |
| **Rein In-Memory** | Übergebene Daten werden nie auf Platte geschrieben oder zwischen Requests gehalten. |
| **Hexagonal** | Fachkern ohne Framework-Abhängigkeiten, Technologie nur in Adaptern. |
| **Prognose mit Fallback** | TimesFM-Inferenz, mit robustem statistischem Schätzer als garantiertem Rückfall. |
| **ERP-agnostisch** | CSV-Spalten werden über Synonymlisten flexibel zugeordnet. |

---

## Architektur

Die Anwendung folgt der **hexagonalen Architektur** (Ports & Adapters). Der
Fachkern kennt weder FastAPI noch pandas, numpy oder TimesFM — er rechnet nur
mit Zahlen. Technologie ist ausschließlich in den Adaptern gebunden und
austauschbar.

```
                  ┌──────────────────────────────────────────┐
   HTTP  ───────► │  src/api.py          (Driving Adapter)   │
                  └───────────────────┬──────────────────────┘
                                      │  SKUInput / AnalysisResult
                  ┌───────────────────▼──────────────────────┐
                  │  src/engine.py       (Anwendungskern)    │
                  │  Orchestrierung + Prognosekette          │
                  └───────┬───────────────────────┬──────────┘
                          │                       │
         ┌────────────────▼─────────┐   ┌─────────▼─────────────────────┐
         │  src/domain/             │   │  src/ports/forecasting.py     │
         │  disposition.py  Formeln │   │  ForecastPort (Protokoll)     │
         │  timeseries.py   Kadenz  │   └─────────┬─────────────────────┘
         │  (keine Framework-Deps)  │             │
         └──────────────────────────┘   ┌─────────▼─────────────────────┐
                                        │  src/adapters/                │
                                        │  timesfm_forecaster.py        │
                                        │  statistical_forecaster.py    │
                                        │  csv_ingest.py                │
                                        └───────────────────────────────┘
```

| Pfad | Rolle |
|---|---|
| `src/schemas.py` | Pydantic-Modelle des API-Vertrags. |
| `src/domain/disposition.py` | Reine Dispositionsformeln und Ampellogik. |
| `src/domain/timeseries.py` | Normalisierung der Historie, Erkennung der Periodenkadenz. |
| `src/ports/forecasting.py` | Port-Protokoll der Bedarfsprognose. |
| `src/adapters/timesfm_forecaster.py` | TimesFM-Inferenz (optional, lazy geladen). |
| `src/adapters/statistical_forecaster.py` | Robuster statistischer Schätzer (Fallback). |
| `src/adapters/csv_ingest.py` | CSV-Import mit flexibler Spaltenzuordnung. |
| `src/engine.py` | Anwendungskern: Prognosekette + Priorisierung. |
| `src/api.py` | FastAPI-Endpunkte. |
| `src/config.py` | Laufzeitkonfiguration über Umgebungsvariablen. |

---

## Rechenmodell

### 1. Prognose

Die Prognosekette wird der Reihe nach durchlaufen; der erste Adapter, der ein
Ergebnis liefert, gewinnt:

1. **TimesFM** — Nullshot-Inferenz des vortrainierten Zeitreihen-Modells
   inklusive Quantilen. Übersprungen, wenn das Paket fehlt, das Checkpoint
   nicht ladbar ist oder die Historie kürzer als `STOCKOUT_TIMESFM_MIN_KONTEXT`
   Perioden ist.
2. **Statistischer Schätzer** — garantiert verfügbarer Fallback:
   * **Trend** über eine Theil-Sen-Regression (Median aller paarweisen
     Steigungen) — unempfindlich gegen einzelne Erfassungsfehler,
   * **gedämpfte Extrapolation** um eine Periode; die Dämpfung wächst mit der
     Länge der Historie,
   * **Sicherheitskorridor** `P90 = P50 + z(0.90) · σ` mit robuster Streuung σ
     aus der Median-Absolutabweichung der Residuen.

Liefert das primäre Modell keine Quantile (`P90 == P50`), ergänzt die Engine
den Korridor aus dem statistischen Schätzer, damit der Sicherheitsbestand nicht
auf 0 kollabiert.

Jede Prognose wird auf einen **30-Tage-Monat normiert**. Die Periodenkadenz
(täglich, wöchentlich, monatlich, …) wird aus den Datumsabständen der Historie
abgeleitet — Tagesdaten ergeben dadurch denselben Tagesbedarf wie äquivalente
Monatsdaten.

### 2. Dispositionsformeln

```
tagesbedarf        = prognostizierter Monatsabsatz / 30
reichweite_tage    = bestand / tagesbedarf            (bei tagesbedarf > 0, sonst 999)
sicherheitsbestand = lieferzeit * (tagesbedarf_P90 - tagesbedarf_P50)
meldebestand       = (tagesbedarf * lieferzeit) + sicherheitsbestand
nachbestellmenge   = max(0, meldebestand - bestand)
```

Alle ausgewiesenen Werte sind auf zwei Nachkommastellen gerundet.
Nachgelagerte Größen werden aus den bereits gerundeten Vorgängern abgeleitet,
damit die veröffentlichten Zahlen exakt zueinander passen
(`nachbestellmenge == meldebestand - bestand`).

Die optionale **Mindestbestellmenge** hebt eine bereits ausgelöste Bestellung
auf die kleinste zulässige Menge an — sie löst selbst nie eine Bestellung aus.

### 3. Status-Ampel

| Status | Bedingung | Bedeutung |
|---|---|---|
| `🔴 KRITISCH` | `reichweite_tage <= lieferzeit` | Der Bestand reicht nicht bis zur nächsten Lieferung. |
| `🟢 OPTIMAL` | `lieferzeit < reichweite_tage <= lieferzeit * 2` | Bestand im Zielkorridor. |
| `🟡 UEBERBESTAND` | `reichweite_tage > lieferzeit * 2` | Überdeckung, Kapitalbindung abbauen. |

Das Feld `status` enthält den Wert inklusive Ampelsymbol für die Anzeige;
`status_code` liefert dieselbe Aussage symbolfrei (`KRITISCH`, `OPTIMAL`,
`UEBERBESTAND`) für Filter und maschinelle Weiterverarbeitung.

Ohne prognostizierten Bedarf gilt die Sentinel-Reichweite **999** Tage.

---

## Schnellstart

```bash
pip install -r requirements.txt
uvicorn src.api:app --reload
```

Interaktive Dokumentation: <http://127.0.0.1:8000/docs> ·
OpenAPI-Schema: <http://127.0.0.1:8000/openapi.json>

### Docker

```bash
docker build -t stockout-sentinel .
docker run --rm -p 8000:8000 stockout-sentinel
```

Das Image ist zweistufig gebaut, enthält keinen Compiler und läuft als
nicht-privilegierter Nutzer. Cloud-Runtimes (Cloud Run, App Service, Fly.io)
geben den Port über `$PORT` vor; die Worker-Zahl steuert `$WEB_CONCURRENCY`.

### Tests

```bash
pytest
```

---

## API

Basis-Pfad: `/api/v1` · Alle Antworten sind `application/json`.

### `GET /health`

Liveness- und Readiness-Probe. Meldet die aktive Prognosekette.

```json
{
  "status": "ok",
  "version": "1.0.0",
  "prognose_kette": ["timesfm", "statistical-theil-sen-p90"],
  "prognose_strategie": "auto"
}
```

---

### `POST /api/v1/analyze-json`

Analysiert eine strukturierte Artikelliste (System-zu-System-Integration).

**Request** — `application/json`, eine Liste von `SKUInput`:

| Feld | Typ | Pflicht | Beschreibung |
|---|---|---|---|
| `sku` | `str` | ja | Artikelnummer oder Bezeichnung. |
| `historie` | `ConsumptionRecord[]` | ja | Mindestens **2** Verbrauchsperioden. |
| `historie[].datum` | `str` | ja | Periodenkennzeichen, z. B. `2024-03-01`, `01.03.2024`, `2024-03`, `Mrz 24`. |
| `historie[].menge` | `float` | ja | Verbrauchsmenge der Periode. |
| `bestand` | `float` | ja | Aktueller Lagerbestand (`>= 0`). |
| `lieferzeit` | `int` | ja | Wiederbeschaffungszeit in Tagen (`>= 0`). |
| `mindestbestellmenge` | `float` | nein | Mindestbestellmenge (MOQ), Standard `0`. |

```bash
curl -X POST http://127.0.0.1:8000/api/v1/analyze-json \
  -H 'Content-Type: application/json' \
  -d '[
        {
          "sku": "A-10045",
          "historie": [
            {"datum": "2024-01-01", "menge": 300},
            {"datum": "2024-02-01", "menge": 310},
            {"datum": "2024-03-01", "menge": 305}
          ],
          "bestand": 20,
          "lieferzeit": 14,
          "mindestbestellmenge": 50
        }
      ]'
```

**Response** — `200 OK`:

```json
{
  "meta": {
    "anzahl_artikel": 1,
    "anzahl_kritisch": 1,
    "anzahl_optimal": 0,
    "anzahl_ueberbestand": 0,
    "gesamt_nachbestellmenge": 141.0,
    "prognose_modelle": ["statistical-theil-sen-p90"],
    "erkannte_spalten": {},
    "warnungen": []
  },
  "ergebnisse": [
    {
      "sku": "A-10045",
      "prognose_tagesbedarf": 10.2,
      "reichweite_tage": 1.96,
      "meldebestand": 161.0,
      "nachbestellmenge": 141.0,
      "status": "🔴 KRITISCH",
      "status_code": "KRITISCH",
      "empfohlene_massnahme": "Sofort 141 Einheiten bestellen: Reichweite 1.96 Tage deckt die Lieferzeit von 14 Tagen nicht (Fehlmenge ab Tag 1.96, 12.04 Tage Unterdeckung).",
      "sicherheitsbestand": 18.2,
      "prognose_tagesbedarf_p90": 11.5,
      "prognose_modell": "statistical-theil-sen-p90",
      "prognose_fallback": true
    }
  ]
}
```

`ergebnisse` ist bereits als **Prioritätenliste** sortiert:

1. Ampel-Rang (`KRITISCH` → `OPTIMAL` → `UEBERBESTAND`),
2. Reichweite aufsteigend (geringste zuerst),
3. Nachbestellmenge absteigend,
4. SKU alphabetisch (stabile, reproduzierbare Reihenfolge).

---

### `POST /api/v1/analyze-csv`

Nimmt den CSV-Export eines beliebigen ERP-Systems entgegen, ordnet die Spalten
im RAM zu und liefert **dieselbe JSON-Matrix** wie `analyze-json`.

**Übergabe** — wahlweise:

```bash
# a) als multipart/form-data
curl -X POST http://127.0.0.1:8000/api/v1/analyze-csv -F 'datei=@export.csv'

# b) als roher Request-Body (Stream)
curl -X POST http://127.0.0.1:8000/api/v1/analyze-csv \
     -H 'Content-Type: text/csv' --data-binary @export.csv
```

Optionaler Query-Parameter `trennzeichen` erzwingt ein festes Trennzeichen;
ohne Angabe wird es erkannt.

**Automatisch erkannt werden:**

* **Trennzeichen** `;` `,` Tab `|`
* **Zeichensätze** UTF-8 (auch mit BOM), CP1252, Latin-1
* **Zahlenformate** `1'234.50` (CH), `1.234,50` (DE), `1,234.50` (EN),
  `CHF 1'000.-`, `(30)` als Negativwert
* **Datumsformate** `2024-03-01`, `01.03.2024`, `2024-03`, `Jan 24`, `Mrz 24`, …

**Spaltenzuordnung** (unempfindlich gegen Groß-/Kleinschreibung, Trennzeichen
und Umlaute — `Art.-Nr.`, `ART NR` und `Art-Nr` sind gleichwertig):

| Zielfeld | Erkannte Spaltennamen (Auszug) |
|---|---|
| `sku` | `Art-Nr`, `Artikelnummer`, `SKU`, `Material`, `Teilenummer`, `Item No`, `Bezeichnung` |
| `bestand` | `Bestand`, `Lager`, `Lagerbestand`, `Ist-Bestand`, `Stock`, `On Hand` |
| `lieferzeit` | `Lieferzeit`, `Vorlaufzeit`, `WBZ`, `Wiederbeschaffungszeit`, `Lead Time` |
| `mindestbestellmenge` | `Mindestbestellmenge`, `MOQ`, `Mindestmenge`, `Losgrösse` |
| `datum` | `Datum`, `Periode`, `Monat`, `Buchungsdatum`, `Date` |
| `menge` | `Verbrauch`, `Absatz`, `Bedarf`, `Abgang`, `Entnahme`, `Menge`, `Quantity` |

**Zwei Tabellenlayouts** werden unterstützt:

*Langformat* — eine Zeile je Artikel und Periode:

```csv
Art-Nr;Datum;Verbrauch;Lager;Vorlaufzeit;MOQ
A-100;01.01.2024;120;450;14;50
A-100;01.02.2024;135;450;14;50
A-100;01.03.2024;150;450;14;50
```

*Breitformat* — eine Zeile je Artikel, Perioden als Datumsspalten:

```csv
SKU,Stock,Lead Time,2024-01,2024-02,2024-03
X-1,80,7,10,12,11
```

Die tatsächlich erkannte Zuordnung wird in `meta.erkannte_spalten`
zurückgemeldet — so ist jederzeit nachvollziehbar, wie der Export gelesen wurde.

**Teilweise unbrauchbare Exporte kippen den Request nicht.** Zeilen ohne
Artikelnummer und Artikel mit weniger als zwei Perioden werden übersprungen und
in `meta.warnungen` gemeldet; der Rest wird ausgewertet.

---

### Fehlerantworten

| Status | Ursache |
|---|---|
| `422` | Leere Artikelliste, Historie mit weniger als 2 Perioden, negativer Bestand, leerer oder nicht interpretierbarer CSV-Stream. |
| `413` | Batch überschreitet `STOCKOUT_MAX_SKU_PRO_REQUEST` oder CSV überschreitet 64 MiB. |

Der Fehlertext in `detail` benennt bei CSV-Problemen die gefundenen Spalten und
die erwarteten Alternativen.

---

## Konfiguration

Alle Stellschrauben werden über Umgebungsvariablen gesetzt (12-Factor);
Standardwerte sind produktionstauglich.

| Variable | Standard | Bedeutung |
|---|---|---|
| `STOCKOUT_PROGNOSE_STRATEGIE` | `auto` | `auto` (TimesFM + Fallback), `timesfm`, `statistisch`. |
| `STOCKOUT_TIMESFM_CHECKPOINT` | `google/timesfm-2.0-500m-pytorch` | Repo-ID oder lokaler Pfad der Modellgewichte. |
| `STOCKOUT_TIMESFM_BACKEND` | `cpu` | `cpu`, `gpu`, `tpu`. |
| `STOCKOUT_TIMESFM_MIN_KONTEXT` | `8` | Mindestzahl Perioden für eine TimesFM-Inferenz. |
| `STOCKOUT_PERIODENLAENGE_TAGE` | `30` | Angenommene Kadenz, wenn sich aus den Daten keine ableiten lässt. |
| `STOCKOUT_MIN_VARIATIONSKOEFF` | `0.10` | Untergrenze der relativen Nachfragestreuung für den Sicherheitskorridor. |
| `STOCKOUT_TREND_DAEMPFUNG` | `0.7` | Dämpfung der Trendfortschreibung. |
| `STOCKOUT_MAX_SKU_PRO_REQUEST` | `20000` | Schutzgrenze gegen überdimensionierte Batches. |

### TimesFM aktivieren

TimesFM ist eine optionale Abhängigkeit und wird erst beim ersten Aufruf
geladen. Ohne das Paket arbeitet der Service unverändert weiter — die Antwort
weist den Rückfall über `prognose_fallback: true` und `prognose_modell` aus.

```bash
pip install "timesfm[torch]"
export STOCKOUT_PROGNOSE_STRATEGIE=auto
```

---

## Datenschutz und Betrieb

* Es werden **keine** übergebenen Artikel-, Verbrauchs- oder Bestandsdaten
  persistiert, protokolliert oder zwischen Requests gehalten.
* Der Service schreibt nichts auf die Platte; CSV-Uploads werden im RAM
  verarbeitet und nach dem Request verworfen.
* Der Container läuft als nicht-privilegierter Nutzer (`uid 10001`).
* `/health` eignet sich als Liveness- und Readiness-Probe.
