# Arbeitsregeln für dieses Repository

Dieses Repository enthält den **Stockout-Sentinel** (Dispositions-Service
unter `src/`) und die **KMU-Problem-Discovery-Engine** (unter
`kmu_discovery/`).

## 1. Nur die Produkte dieses Repositorys

Arbeite ausschliesslich an Stockout-Sentinel und der Discovery-Engine. Kommt
eine Frage oder ein Auftrag zu einem anderen Produkt — **Inbox Copilot** oder
**Confident Style** —, dann **melde das kurz und bearbeite es nicht**, auch
dann nicht, wenn du den Code dafür kennst. Jedes Produkt hat sein eigenes
Repository und seine eigene Sitzung.

Der Inbox Copilot liegt in
<https://github.com/harmweber787-png/inbox-copilot>. Der Ordner
`inbox-copilot/` hier ist eine Altlast des Umzugs und wird nach Freigabe
durch Harm entfernt — bis dahin **nicht** weiterentwickeln, Änderungen
gehören ins neue Repository.

## 2. Keine Wecker, keine Selbst-Erinnerungen

Setze **keine** Check-in-Erinnerungen, Routinen, Cron-Trigger oder
zeitgesteuerten Wecker — es sei denn, Harm bittet in der laufenden Sitzung
ausdrücklich darum.

Hintergrund: In der Nacht vom 20. auf den 21.09.2026 haben sich zwei
Sitzungen rund fünfzigmal selbst geweckt, ohne dass sich etwas bewegt hat.
Ein Wecker, der nichts zu tun findet, ist kein harmloser Leerlauf.

Wenn du auf etwas wartest (CI, ein Review, eine Freigabe): sag es und beende
den Zug. Harm meldet sich.

## 3. Nichts mergen, schliessen oder löschen ohne Freigabe

Pull Requests **mergen oder schliessen**, Branches oder Dateien **löschen**:
alles nur nach ausdrücklicher Freigabe von Harm in der laufenden Sitzung.
Eine Freigabe aus einer früheren Sitzung oder für einen anderen Vorgang
zählt nicht.

Erstellen, ändern und pushen auf einem Arbeitsbranch ist frei; einen PR
öffnen auch. Der letzte Schritt gehört Harm.

## 4. Am Ende jeder Sitzung: STATUS.md aktualisieren

`STATUS.md` ist der Übergabepunkt zwischen den Sitzungen. Trage vor dem
Abschluss nach, was sich geändert hat — Stand, Erledigtes, Offenes, was auf
Harm wartet, offene PRs. Kurz halten, höchstens eine Bildschirmseite.

---

## Zu den Projekten

**Prüfkette** (aus dem Wurzelverzeichnis; genau das fährt auch die CI):

```bash
pytest
ruff check kmu_discovery tests_kmu_discovery scripts
mypy --strict --config-file pyproject.toml
```

`pytest` braucht die leichten Laufzeit-Abhängigkeiten aus
`requirements-kmu-discovery.txt` plus fastapi, pandas, numpy und streamlit;
`torch` und `timesfm` bleiben aussen vor (die Alt-Tests laufen mit dem
Fake-Forecaster aus `tests/conftest.py`).

**Discovery-Engine:** Jede Datenquelle wird erst live erhoben
(`scripts/probe_*.py`), dann wird der Adapter auf die Erhebung gestützt —
kein Feldname aus dem Gedächtnis. Erhebungsergebnisse liegen unter `docs/`.
Publikationstexte enthalten Personendaten aus öffentlichen Registern: nur
als gekürztes Zitat belegen, nie als Dokument ans Profil hängen (revDSG).

**Stockout-Sentinel:** TimesFM ist im Standardbetrieb das verbindliche
Hauptmodell (`FORCE_TIMESFM=true`), ohne statistischen Rückfall. Der
Fachkern unter `src/domain/` kennt weder FastAPI noch pandas.
