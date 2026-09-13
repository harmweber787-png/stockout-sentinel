# Stockout-Sentinel

Generischer, produktionsreifer Dispositions-Service für B2B-Lieferketten.

Stockout-Sentinel prognostiziert je Artikel den künftigen Bedarf, berechnet
Meldebestand und Nachbestellmenge und liefert eine nach Dringlichkeit sortierte
**Prioritätenliste** zurück.

Der Service ist bewusst **offen und branchenneutral**: Er enthält keine
hinterlegten Stamm-, Beispiel- oder Sortimentsdaten. Analysiert wird
ausschließlich, was im Request übergeben wird.

> ### ⚠️ TimesFM ist das verbindliche Hauptmodell
>
> Im Standardbetrieb (`FORCE_TIMESFM=true`) läuft **jede** Prognose über das
> TimesFM-Foundation-Model von Google, geladen direkt vom Hugging-Face-
> Checkpoint `google/timesfm-2.5-200m-pytorch`.
>
> **Der Fallback ist vollständig blockiert.** Das hat Betriebsfolgen, die vor
> dem Deployment bekannt sein müssen:
>
> * Lässt sich das Checkpoint beim Start nicht laden, **startet der Service
>   nicht** (Fail-Fast statt stiller Falschrechnung).
> * Scheitert eine Inferenz zur Laufzeit, antwortet der Endpunkt mit **`503`** —
>   nicht mit Zahlen aus einem Ersatzverfahren.
> * Damit ist die Verfügbarkeit der Disposition an die Verfügbarkeit des
>   Modells gekoppelt. Für Deployments ohne Internetzugang die Gewichte ins
>   Image backen oder als Volume mounten (siehe [Modellgewichte](#modellgewichte)).
>
> Für einen Notbetrieb lässt sich der Schalter mit `FORCE_TIMESFM=false`
> abschalten; dann greift wieder die frühere Kette mit statistischem Rückfall.

---

## Eigenschaften

| Eigenschaft | Umsetzung |
|---|---|
| **Zustandslos** | Jeder Request ist unabhängig; horizontal skalierbar. |
| **Rein In-Memory** | Übergebene Daten werden nie auf Platte geschrieben oder zwischen Requests gehalten. |
| **Hexagonal** | Fachkern ohne Framework-Abhängigkeiten, Technologie nur in Adaptern. |
| **TimesFM verbindlich** | Neuronale Prognose als Pflichtmodell, Rückfall blockiert (`FORCE_TIMESFM`). |
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
                                        │  timesfm_forecaster.py  (!)   │
                                        │  statistical_forecaster.py    │
                                        │  csv_ingest.py                │
                                        │  (!) Pflichtmodell            │
                                        └───────────────────────────────┘
```

| Pfad | Rolle |
|---|---|
| `src/schemas.py` | Pydantic-Modelle des API-Vertrags. |
| `src/domain/disposition.py` | Reine Dispositionsformeln und Ampellogik. |
| `src/domain/timeseries.py` | Normalisierung der Historie, Erkennung der Periodenkadenz. |
| `src/ports/forecasting.py` | Port-Protokoll der Bedarfsprognose. |
| `src/adapters/timesfm_forecaster.py` | TimesFM-Inferenz — **Pflichtmodell**, lazy geladen. |
| `src/adapters/statistical_forecaster.py` | Robuster statistischer Schätzer — nur im Notbetrieb (`FORCE_TIMESFM=false`). |
| `src/adapters/csv_ingest.py` | CSV-Import mit flexibler Spaltenzuordnung. |
| `src/engine.py` | Anwendungskern: Prognosekette + Priorisierung. |
| `src/api.py` | FastAPI-Endpunkte. |
| `src/config.py` | Laufzeitkonfiguration über Umgebungsvariablen. |

---

## Rechenmodell

### 1. Prognose

#### Standardbetrieb: `FORCE_TIMESFM=true`

Die Prognosekette besteht **ausschließlich** aus dem TimesFM-Adapter. Der
statistische Schätzer wird in diesem Modus gar nicht erst instanziiert, und die
Engine weist eine Kette, in die ein anderer Adapter eingeschleust wurde, beim
Aufbau mit einem `ValueError` zurück.

* **Inferenz** — Nullshot-Prognose des vortrainierten Modells inklusive
  Quantilraster (`[batch, horizon, 10]`: Index 0 = Punktprognose,
  Indizes 1–9 = Quantile 0.1–0.9).
* **P50/P90** werden den Indizes 5 und 9 entnommen. `fix_quantile_crossing`
  ist aktiviert, damit das P90 nie unter das P50 fallen kann.
* **Kein Ersatzkorridor.** Liefert das Modell kein auswertbares Quantilraster,
  scheitert die Inferenz — der Korridor wird *nicht* statistisch ergänzt, denn
  auch das wäre ein Rückfall.
* **Kein stilles Überspringen.** Scheitert die Inferenz für einen einzigen
  Artikel, scheitert der ganze Request. Eine Prioritätenliste, in der Artikel
  unbemerkt fehlen, wäre für die Disposition gefährlicher als ein klarer Fehler.

#### Notbetrieb: `FORCE_TIMESFM=false`

Nur in diesem Modus existiert die frühere Kette mit Rückfall auf den robusten
statistischen Schätzer:

* **Trend** über eine Theil-Sen-Regression (Median aller paarweisen
  Steigungen) — unempfindlich gegen einzelne Erfassungsfehler,
* **gedämpfte Extrapolation** um eine Periode; die Dämpfung wächst mit der
  Länge der Historie,
* **Sicherheitskorridor** `P90 = P50 + z(0.90) · σ` mit robuster Streuung σ
  aus der Median-Absolutabweichung der Residuen.

#### Normierung (in beiden Modi)

Jede Prognose wird auf einen **30-Tage-Monat normiert**. Die Periodenkadenz
(täglich, wöchentlich, monatlich, …) wird aus den Datumsabständen der Historie
abgeleitet — Tagesdaten ergeben dadurch denselben Tagesbedarf wie äquivalente
Monatsdaten. Monatserste liegen im Median 31 Tage auseinander, eine konstante
Historie von 300/Monat ergibt daher einen Tagesbedarf von `300 · (30/31) / 30 =
9.68` statt glatt 10.

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
uvicorn src.api:app
```

Beim ersten Start lädt der Service das TimesFM-Checkpoint von Hugging Face
(~1 GB). Das dauert einige Minuten und **benötigt Netzzugang zu
`huggingface.co`** — ohne das startet der Service nicht.

Interaktive Dokumentation: <http://127.0.0.1:8000/docs> ·
OpenAPI-Schema: <http://127.0.0.1:8000/openapi.json>

> **Hinweis zu `torch`:** Von PyPI zieht `pip install torch` die CUDA-Variante
> (mehrere GB). Für CPU-Deployments stattdessen:
> `pip install torch --index-url https://download.pytorch.org/whl/cpu`
> Das Dockerfile tut das bereits standardmäßig.

### Docker

```bash
docker build -t stockout-sentinel .
docker run --rm -p 8000:8000 stockout-sentinel
```

Das Image ist zweistufig gebaut, enthält keinen Compiler und läuft als
nicht-privilegierter Nutzer. Cloud-Runtimes (Cloud Run, App Service, Fly.io)
geben den Port über `$PORT` vor; die Worker-Zahl steuert `$WEB_CONCURRENCY`
(Standard `1` — jeder Worker hält eine eigene Modellkopie im Speicher).

Für GPU-Images die CUDA-Wheels wählen:

```bash
docker build --build-arg TORCH_INDEX_URL=https://pypi.org/simple \
             -t stockout-sentinel-gpu .
```

### Modellgewichte

Der Start lädt das Checkpoint vom Hugging-Face-Hub. Für abgeschottete
Umgebungen und schnelle Kaltstarts gibt es zwei Alternativen:

```dockerfile
# a) Gewichte ins Image backen (vergrößert es um ~1 GB)
RUN python -c "import timesfm; \
    timesfm.TimesFM_2p5_200M_torch.from_pretrained('google/timesfm-2.5-200m-pytorch')"
```

```bash
# b) Gewichte als Volume mounten und lokal laden
docker run -v /opt/timesfm:/models:ro \
           -e STOCKOUT_TIMESFM_CHECKPOINT=/models/timesfm-2.5-200m \
           -p 8000:8000 stockout-sentinel
```

`STOCKOUT_TIMESFM_CHECKPOINT` akzeptiert sowohl eine HF-Repo-ID als auch einen
lokalen Verzeichnispfad.

### Tests

```bash
pytest
```

Die Suite läuft **ohne Netzzugang und ohne Modellgewichte**: Formeln, CSV-Import
und API werden im Notbetrieb geprüft, das Pflichtmodell-Verhalten gegen ein
Modell-Double mit der verifizierten TimesFM-Ausgabeform. Damit bleibt CI
unabhängig von der Erreichbarkeit des Hugging-Face-Hubs.

---

## API

Basis-Pfad: `/api/v1` · Alle Antworten sind `application/json`.

### `GET /health`

Liveness- und Readiness-Probe. Meldet die aktive Prognosekette.

```json
{
  "status": "ok",
  "version": "1.0.0",
  "prognose_kette": ["timesfm"],
  "prognose_strategie": "timesfm",
  "force_timesfm": true,
  "timesfm_checkpoint": "google/timesfm-2.5-200m-pytorch",
  "modell_geladen": true,
  "fallback_erlaubt": false
}
```

`status` ist `"degraded"`, solange das Pflichtmodell nicht geladen ist. Der
Docker-HEALTHCHECK wertet `modell_geladen` aus.

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
    "prognose_modelle": ["timesfm:google/timesfm-2.5-200m-pytorch"],
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
      "prognose_modell": "timesfm:google/timesfm-2.5-200m-pytorch",
      "prognose_fallback": false
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
| `503` | Die TimesFM-Inferenz ist gescheitert. Da der Fallback blockiert ist, gibt es bewusst **kein** Ersatzergebnis — die Antwort dürfte sonst so aussehen, als sei sie vom freigegebenen Modell gerechnet. |

Der Fehlertext in `detail` benennt bei CSV-Problemen die gefundenen Spalten und
die erwarteten Alternativen; bei `503` die konkrete Ursache des Modellfehlers.

---

## Konfiguration

Alle Stellschrauben werden über Umgebungsvariablen gesetzt (12-Factor);
Standardwerte sind produktionstauglich.

| Variable | Standard | Bedeutung |
|---|---|---|
| **`FORCE_TIMESFM`** | **`true`** | **TimesFM ist Pflichtmodell, jeder Rückfall blockiert.** `false` schaltet den Notbetrieb frei. |
| `STOCKOUT_TIMESFM_CHECKPOINT` | `google/timesfm-2.5-200m-pytorch` | HF-Repo-ID **oder** lokaler Pfad der Modellgewichte. |
| `STOCKOUT_TIMESFM_BACKEND` | `cpu` | `cpu`, `gpu`. |
| `STOCKOUT_TIMESFM_MIN_KONTEXT` | `2` (Pflichtmodus) / `8` | Mindestzahl Perioden für eine Inferenz. Im Pflichtmodus bewusst auf dem Schema-Minimum, damit wirklich jeder Artikel über TimesFM läuft — das Modell padded kürzere Kontexte selbst. |
| `STOCKOUT_TIMESFM_MAX_KONTEXT` | `512` | Maximale Kontextlänge; längere Reihen werden auf die jüngsten Perioden gekürzt. |
| `STOCKOUT_TIMESFM_TORCH_COMPILE` | `false` | `torch.compile` aktivieren: schnellerer Dauerbetrieb, langsamerer erster Aufruf. |
| `STOCKOUT_PROGNOSE_STRATEGIE` | *(im Pflichtmodus ignoriert)* | Nur bei `FORCE_TIMESFM=false`: `auto`, `timesfm`, `statistisch`. |
| `STOCKOUT_PERIODENLAENGE_TAGE` | `30` | Angenommene Kadenz, wenn sich aus den Daten keine ableiten lässt. |
| `STOCKOUT_MIN_VARIATIONSKOEFF` | `0.10` | Untergrenze der relativen Nachfragestreuung (nur statistischer Pfad). |
| `STOCKOUT_TREND_DAEMPFUNG` | `0.7` | Dämpfung der Trendfortschreibung (nur statistischer Pfad). |
| `STOCKOUT_MAX_SKU_PRO_REQUEST` | `20000` | Schutzgrenze gegen überdimensionierte Batches. |
| `LOG_LEVEL` | `INFO` | Log-Schwelle; das Laden des Pflichtmodells wird auf `INFO` protokolliert. |

`STOCKOUT_PROGNOSE_STRATEGIE` kann den Pflichtmodus **nicht** aushebeln: bei
`FORCE_TIMESFM=true` wird die Strategie auf `timesfm` festgezurrt.

### Unterstützte TimesFM-Generationen

Der Adapter erkennt die installierte Paketgeneration selbst:

| Paket | Aufrufweg |
|---|---|
| `timesfm` 3.x / 2.5 *(Standard)* | `TimesFM_2p5_200M_torch.from_pretrained(repo)` → `compile(ForecastConfig)` → `forecast(horizon, inputs)` |
| `timesfm` 2.0 / 1.x *(Bestand)* | `TimesFm(TimesFmHparams, TimesFmCheckpoint)` → `forecast(inputs, freq)` |

---

## Datenschutz und Betrieb

* Es werden **keine** übergebenen Artikel-, Verbrauchs- oder Bestandsdaten
  persistiert, protokolliert oder zwischen Requests gehalten.
* Der Service schreibt nichts auf die Platte; CSV-Uploads werden im RAM
  verarbeitet und nach dem Request verworfen. Einzige Ausnahme ist der
  Modell-Cache unter `$HF_HOME` — dort liegen ausschließlich die öffentlichen
  TimesFM-Gewichte, niemals Kundendaten.
* Die Verbrauchsreihen werden lokal an das Modell übergeben; es findet **kein**
  Aufruf einer externen Inferenz-API statt. Netzzugang wird nur einmalig beim
  Laden der Gewichte benötigt.
* Der Container läuft als nicht-privilegierter Nutzer (`uid 10001`).
* `/health` eignet sich als Liveness- und Readiness-Probe; `modell_geladen`
  unterscheidet dabei „Prozess lebt" von „Pflichtmodell einsatzbereit".

### Verfügbarkeitsrisiko

Mit blockiertem Fallback ist die Disposition nur so verfügbar wie das Modell.
Fällt das Laden aus — gesperrter Egress, HF-Ausfall, fehlendes Volume —, steht
der Service vollständig. Das ist die bewusste fachliche Entscheidung hinter
`FORCE_TIMESFM`: lieber sichtbar nicht antworten als eine Bestellempfehlung
ausgeben, die nicht aus dem freigegebenen Modell stammt. Wer das Risiko senken
will, backt die Gewichte ins Image (siehe [Modellgewichte](#modellgewichte))
statt sie beim Start zu ziehen.
