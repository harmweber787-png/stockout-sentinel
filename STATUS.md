# Status — Stockout-Sentinel und KMU-Discovery-Engine

## Stand (24.09.2026)

Dieses Repository enthält seit heute nur noch zwei Produkte. Der Inbox
Copilot ist ausgezogen (siehe unten).

**KMU-Problem-Discovery-Engine** (`kmu_discovery/`) — der aktive Teil. Drei
Datenquellen sind angebunden, jede nach demselben Vorgehen: erst live
erheben, dann den Adapter auf die Erhebung stützen.

| Quelle | Was sie liefert |
|---|---|
| LINDAS | Stammdaten aus dem Linked-Data-Dienst des Bundes |
| Zefix | Handelsregister-Auszüge über die offizielle REST-API |
| SHAB | Handelsregister-Mutationen als Kontaktanlässe (seit 24.09.) |

Gemeinsamer Unterbau in `sources/base.py`: Token-Bucket, Backoff, typisierte
Fehler, dateibasierter Rohdaten-Cache. Prüfkette grün — `pytest`,
`ruff check` und `mypy --strict` über 40 Dateien.

**Stockout-Sentinel** (`src/`) — der ursprüngliche Dispositions-Service,
derzeit ohne aktive Weiterentwicklung. TimesFM als verbindliches
Hauptmodell, Fallback blockiert; die Tests laufen in der CI mit dem
Fake-Forecaster, ohne `torch` und `timesfm`.

CI: ein Job, `checks`. `main` ist grün.

## Erledigt in der letzten Sitzung

- **#3 gemergt:** SHAB-Quelle (Adapter, Messinstrument, ~1'000 Zeilen
  Tests) plus der Fix, der die Cache-Frische an der Uhr misst, die den
  Zeitstempel gesetzt hat. Letzterer hat die beiden zeitabhängigen Tests
  `test_wiederholter_abruf_kommt_aus_dem_cache` repariert, die seit dem
  18.09. auf `main` und damit auf jedem PR rot waren.
- **#2, #6, #7 gemergt:** Inbox Copilot als Unterordner,
  SDK-1.x-Kompatibilität, zweiter CI-Job.
- **#8 gemergt:** `CLAUDE.md` mit den Arbeitsregeln und diese `STATUS.md`.
- **#9 gemergt:** `inbox-copilot/` entfernt, der zweite CI-Job entfällt,
  README und CLAUDE.md verweisen auf das neue Repository.
- **Inbox Copilot ausgezogen** nach
  <https://github.com/harmweber787-png/inbox-copilot> — mit History, der
  Ordner ist dort zur Wurzel geworden. Dateistand byteweise deckungsgleich
  (SHA-256 `fb0a9e33…1ab21e` auf beiden Seiten), CI dort beim ersten Lauf
  grün.

## Offen

- **Vierte Datenquelle.** Nach LINDAS, Zefix und SHAB ist der nächste
  Kontaktanlass noch nicht festgelegt.
- **`confident-style/` aus PR #5** gehört nicht in dieses Repository. Der
  Export liegt als Archiv bereit; das Zielrepository ist angelegt, aber für
  die Sitzung nicht freigegeben (siehe unten).

## Wartet auf Harm

- **Freigabe für `harmweber787-png/confident-style`.** Die
  Claude-GitHub-App ist dort nicht installiert, deshalb schlägt der Push
  fehl. Harm erledigt das selbst und meldet sich; bis dahin wird nichts
  unternommen.
- **Entscheidung über PR #5.** Erst wenn der Export im neuen Repository
  liegt, wird #5 mit Verweis dorthin geschlossen.

## Offene PRs

| # | Titel | Status |
|---|---|---|
| 5 | Add Confident Style MVP: photo-based clothing and hair advice | Draft, konfliktfrei. CI grün, prüft aber nur Python — die ~9'000 Zeilen TypeScript hat nie eine Prüfung gesehen. Soll nicht gemergt, sondern nach dem Export geschlossen werden. |
