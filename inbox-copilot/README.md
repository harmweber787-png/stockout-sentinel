# inbox-copilot

Inbox & Operations Copilot für Schweizer KMU – Modul M-1 "Inbox Triage & Drafter".

Eingehende E-Mails werden in zwei Stufen verarbeitet: Stufe 1 klassifiziert
(Kategorie, Dringlichkeit, Frist, Risiko), Stufe 2 schreibt einen Antwortentwurf.
Der Entwurf landet als **Draft im Gmail-Postfach** – mit Warnbox, `[PRÜFEN]`-Betreff
und gelb markierten Platzhaltern. **Es wird nie automatisch gesendet.** Der Inhaber
prüft jeden Entwurf.

Leitplanken (unverändert aus Teil 1): Code schlägt Modell · revDSG / Zero-Data-Retention
(keine Namen, Adressen, Betreffe oder Texte in Logs, State, Terminal oder Exceptions) ·
Schweizer Rechtschreibung · Idempotenz · Mail-Inhalt ist Daten, nie Anweisung.

```
inbox_copilot/
  config.py         Settings, Model-IDs, Limits, Labels, alle Regex, System-Prompts
  schemas.py        Pydantic-Modelle (Payloads, LLM-Ausgaben, Gates, Audit, Stats)
  pipeline.py       Zweistufige Pipeline mit Gates, Retry-Engine, Renderer
  gmail_ingest.py   Gmail-JSON -> Pipeline-Payloads (Body, Zitate, Signatur, PDF)
  gmail_adapter.py  MailAdapter fuer Gmail (Labels, Drafts, Backoff, OAuth)
  service.py        Orchestrierung, Idempotenz-Ringpuffer, Metriken
  runner.py         Duenne CLI
customers/          Kundenprofile (YAML)
tests/              52 netzfreie Tests (T01-T22, I01-I12, A01-A06, H01-H08, S01-S04)
```

---

## Installation

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest && mypy && ruff check . && ruff format --check .
```

---

## Live-Test-Anleitung (Beat-Meier-Testmail)

Ziel: Die Mängelrüge "Beat Meier" (SIA 118) landet als formatierter Draft im Ordner
**Entwürfe** eines echten Gmail-Kontos – ohne Doppelverarbeitung.

### 1. Google Cloud vorbereiten

1. <https://console.cloud.google.com> → neues Projekt anlegen (z. B. `inbox-copilot-test`).
2. **APIs & Dienste → Bibliothek** → "Gmail API" → **Aktivieren**.
3. **APIs & Dienste → OAuth-Zustimmungsbildschirm** → Typ *Extern* → App-Name und
   Kontakt eintragen → unter **Testnutzer** die Gmail-Adresse des Testkontos hinzufügen.
   Ohne diesen Eintrag endet der Browser-Flow mit `access_denied`.
4. **APIs & Dienste → Anmeldedaten → Anmeldedaten erstellen → OAuth-Client-ID** →
   Anwendungstyp **Desktop-App** → JSON herunterladen.
5. Die Datei im Projekt als `secrets/oauth_client.json` ablegen (Ordner `secrets/`
   anlegen; er ist per `.gitignore` ausgeschlossen).

### 2. Anthropic-Schlüssel

```bash
cp .env.example .env
# .env oeffnen und ANTHROPIC_API_KEY=sk-ant-... eintragen
```

### 3. Gmail autorisieren

```bash
python -m inbox_copilot.runner --customer weber --auth
```

Der Browser öffnet sich, das Testkonto auswählen, den Hinweis "Google hat diese App
nicht überprüft" mit *Erweitert → Weiter* bestätigen, Zugriff **"E-Mails lesen,
verfassen und dauerhaft löschen"** erteilen (das ist die Google-Beschreibung des
Scopes `gmail.modify`; der Copilot löscht nichts – siehe Abschnitt 8). Danach steht
`auth: Token gespeichert` im Terminal; der Token liegt unter `secrets/token.json`.

`--customer weber` lädt `customers/weber.yaml`, ersatzweise `customers/weber.example.yaml`.
Für den eigenen Betrieb die Beispieldatei kopieren und Firma, Inhaber, Ort und
Signatur anpassen.

### 4. Testmail senden

Von einem **zweiten** Konto an das Testkonto senden:

- Betreff: `Maengelruege Fassade Bauvorhaben Urdorf`
- Text (Wortlaut aus Teil 1):

  ```
  Guten Tag Herr Weber

  An der Fassade des Bauvorhabens in Urdorf zeigen sich Risse im Verputz. Wir ruegen den Mangel hiermit formell und setzen Ihnen gemaess SIA 118 eine Frist bis 25. September 2026 zur Nachbesserung.

  Freundliche Gruesse
  Beat Meier
  ```

- Anhang: `maengelliste_protokoll.pdf` mit Textlayer (kein reiner Scan). Der Dateiname
  löst die Anhang-Extraktion aus; die ersten vier und die letzte Seite gehen als
  Auszug an Stufe 2.

Bis zu 30 Sekunden warten – der Gmail-Suchindex ist nicht sofort konsistent.

### 5. Einmal ausführen

```bash
python -m inbox_copilot.runner --customer weber --once
```

**Erwartete Terminal-Ausgabe** (eine Zeile pro Nachricht, nie Betreff oder Absender):

```
3f9c2a71 | ESCALATED_WITH_DRAFT | AI/10-Triaged,AI/99-Achtung-Chef,AI/21-Draft-Platzhalter | 8420
run_once: 1 Nachrichten | {'ESCALATED_WITH_DRAFT': 1}
```

Format: `hash[:8] | STATUS | labels | total_ms`.

**Erwartetes Bild in Gmail:**

- Die Testmail trägt drei Labels: `AI/10-Triaged`, `AI/99-Achtung-Chef` (rot) und
  `AI/21-Draft-Platzhalter` (lachs). Das Elternlabel `AI` legt Gmail selbst an.
- Der Draft erscheint **im Thread** direkt unter der Testmail **und** unter **Entwürfe**.
- Betreff: `[PRÜFEN] Re: Maengelruege Fassade Bauvorhaben Urdorf` (bzw. der vom
  Modell gekürzte Betreff).
- Empfänger ist bereits eingetragen (Absender der Testmail).
- Screenshot-Beschreibung: Oben ein gelber Kasten mit rotem Rand
  "⚠️ INTERNER KI-HINWEIS (Vor dem Senden diesen Kasten löschen)" und dem
  Prüfhinweis (Platzhalter, offen gelassene Frist, Anhang lesen). Darunter eine
  Trennlinie. Darunter der Entwurf: Anrede, neutrale Eingangsbestätigung, die
  genannte Frist "zur Kenntnis genommen" (nie bestätigt), Rückmeldung bis
  **`[[RUECKMELDUNG_BIS: Datum wählen]]`** – gelb hinterlegt –, Telefonangebot,
  "Freundliche Grüsse" und die Signatur aus der Kundendatei.

Ein zweiter `--once`-Lauf gibt `SKIPPED_ALREADY_PROCESSED` aus und ruft weder Gmail
noch das Modell erneut auf.

### 6. Demo-Modus (Video)

```bash
python -m inbox_copilot.runner --customer weber --loop --interval 10
```

Reaktionszeit ≈ Intervall + Pipeline-Latenz (typisch 10–25 s). Gegenüber Kunden
"innert einer Minute" kommunizieren; der Standardwert für den Betrieb ist 60 s.
`Ctrl+C` beendet sauber. Kennzahlen: `--stats` (JSON, PII-frei).

### 7. Fehlerbilder

| Symptom | Ursache | Massnahme |
|---|---|---|
| Kein Label an der Mail, Terminal zeigt nichts | Query greift nicht (Mail älter als 2 Tage, in Werbung/Soziales) oder Index noch nicht aktuell | 30 s warten, erneut `--once`; sonst `POLL_QUERY` in `.env` anpassen |
| Label 10 + 99, aber kein Draft | Stufe 2 ist fatal gescheitert (z. B. Zusage im Entwurf nach Retry) | Audit-Log lesen: `error_code` z. B. `DRAFT_ZUSAGE`; Mail von Hand beantworten |
| `HTTP 400` vom Modell | Sampling-Parameter an ein Modell, das sie nicht mehr annimmt | `MODELLE_OHNE_SAMPLING_PARAMETER` in `config.py` um das Modell ergänzen |
| `Invalid label color` im Log (`LABEL_COLOR_REJECTED`) | Farbwert nicht aus der Google-Palette | Label wird ohne Farbe angelegt; `label_colors` in `config.py` korrigieren |
| `GMAIL_AUTH_REQUIRED` oder `invalid_grant` | Token abgelaufen oder widerrufen (Testnutzer-Token laufen nach 7 Tagen ab) | `secrets/token.json` löschen, `--auth` wiederholen |
| `Incorrect padding` | Base64url ohne Padding an `base64.b64decode` übergeben | Nur `decode_base64url_bytes` verwenden – die Pipeline tut das bereits |
| `ANTHROPIC_API_KEY fehlt` | `.env` fehlt oder leer | Abschnitt 2 |
| `GMAIL_HTTP_403` | Gmail API im Projekt nicht aktiviert oder falscher Scope im Token | API aktivieren; Token löschen und `--auth` |

Audit-Log: JSON-Zeilen auf stdout mit `message_id_hash`, Status, Latenzen, Tokens,
Gate-Codes – nie mit Inhalt.

### 8. Hinweis für den Datenschutz-Zusatz

Der Copilot verwendet ausschliesslich den Scope `https://www.googleapis.com/auth/gmail.modify`:
lesen, Labels setzen, Entwürfe anlegen. **Er sendet nicht und löscht nicht.** Mail-Inhalte
werden nur im Arbeitsspeicher verarbeitet und an das Modell (Anthropic) übermittelt;
lokal verbleiben ausschliesslich Gmail-IDs (`state/gmail_state.json`), Hashes und
Kennzahlen. Anhänge werden nie auf Disk geschrieben.

---

## Betriebsparameter

Alle Werte stehen in `inbox_copilot/config.py` und sind per Umgebungsvariable
überschreibbar (`TRIAGE_MODEL`, `DRAFTER_MODEL`, `POLL_INTERVAL_S`, `POLL_QUERY`,
`GMAIL_STATE_PATH`, …). Model-IDs vor dem Deployment gegen
<https://docs.claude.com/en/api/overview> verifizieren.
