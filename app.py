"""Streamlit-Oberflaeche fuer den Stockout-Sentinel.

Ein Treiber-Adapter wie ``src/api.py``, nur mit Menschen statt Maschinen am
anderen Ende: Die Oberflaeche uebersetzt Eingaben in Aufrufe der bestehenden
Engine und stellt deren Ergebnisse dar. Fachlogik enthaelt sie nicht - alle
Zahlen stammen aus ``src.engine`` und dem TimesFM-Adapter.

Drei Zonen:
    1. **Eingabe**     - CSV-Upload, Demo-Szenarien, Prognosehorizont.
    2. **Radar**       - Verlauf: Historie, Prognosekurve, P10/P90-Korridor.
    3. **Entscheidung**- Ampel-Tabelle mit Reichweite und Nachbestellmenge.

Start:
    streamlit run app.py

Hinweis zum Prognosemodell: Im Standardbetrieb ist TimesFM Pflichtmodell
(``FORCE_TIMESFM=true``). Laesst es sich nicht laden, zeigt die Oberflaeche
das als Fehler an und rechnet **nicht** mit einem Ersatzverfahren weiter.
Zum Ausprobieren ohne Modellgewichte: ``FORCE_TIMESFM=false`` setzen.
"""

from __future__ import annotations

import io
import math
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from src.adapters.csv_ingest import CSVIngestFehler, lese_csv
from src.adapters.timesfm_forecaster import TimesFMForecaster
from src.config import lade_config
from src.demo_daten import DEMO_SZENARIEN, als_csv, baue_szenario
from src.domain.disposition import KEINE_REICHWEITE, Status
from src.domain.timeseries import parse_datum
from src.engine import baue_engine
from src.ports.forecasting import ForecastPfad, ForecastUnavailable
from src.schemas import AnalysisResult, SKUInput

__all__ = [
    "main",
    "horizont_in_perioden",
    "verlaufsrahmen",
    "text_zu_rohdaten",
    "waehle_csv_quelle",
]

TITEL = "Stockout-Sentinel"

#: Schluessel im Session-State: der zuletzt *bestaetigte* Textfeldinhalt.
#: Ohne diese Ablage waere der Inhalt nach dem naechsten Rerun - etwa beim
#: Verschieben des Horizont-Reglers - wieder verloren, denn ein Button
#: meldet seinen Druck nur im unmittelbar folgenden Durchlauf.
SCHLUESSEL_TEXT = "bestaetigter_csv_text"

#: Farbwahl bewusst kontrastreich und nicht rot/gruen-abhaengig - die Ampel
#: traegt ihre Aussage ohnehin im Symbol, der Graph soll unabhaengig davon
#: lesbar bleiben.
FARBE_HISTORIE = "#1f3a5f"
FARBE_PROGNOSE = "#d97706"
FARBE_KORRIDOR = "#fbbf24"

#: Ampelfarben fuer die Hinterlegung der Entscheidungstabelle.
AMPEL_HINTERGRUND = {
    Status.KRITISCH.value: "background-color: rgba(220, 38, 38, 0.14)",
    Status.OPTIMAL.value: "background-color: rgba(22, 163, 74, 0.12)",
    Status.UEBERBESTAND.value: "background-color: rgba(234, 179, 8, 0.16)",
}


# ---------------------------------------------------------------------------
# Reine Hilfsfunktionen (ohne Streamlit, damit testbar)
# ---------------------------------------------------------------------------
def horizont_in_perioden(
    horizont_tage: float, periodenlaenge_tage: float, *, obergrenze: int = 128
) -> int:
    """Rechnet einen Horizont in Tagen in Perioden der Reihe um.

    Die Kadenz stammt aus der Historie: Bei Monatsdaten ergeben 90 Tage drei
    Perioden, bei Tagesdaten 90. Das Ergebnis liegt zwischen 1 und
    ``obergrenze`` - TimesFM kann nicht beliebig weit vorausrechnen.
    """
    if periodenlaenge_tage <= 0:
        raise ValueError("periodenlaenge_tage muss groesser als 0 sein.")
    perioden = math.ceil(float(horizont_tage) / float(periodenlaenge_tage))
    return max(1, min(int(perioden), int(obergrenze)))


def text_zu_rohdaten(text: str) -> bytes:
    """Bereitet eingefuegten CSV-Text als Bytestrom fuer den Importer auf.

    Beim Kopieren aus Messengern, Notiz-Apps oder von einem Smartphone
    schleichen sich regelmaessig ein BOM, geschuetzte Leerzeichen und
    gemischte Zeilenenden ein - Letztere hat diese Codebasis schon einmal
    Zeit gekostet. ``io.StringIO`` liest den Text zeilenweise wie eine
    Datei, sodass der Importer denselben Strom bekommt wie bei einem
    echten Upload.
    """
    bereinigt = text.lstrip("\ufeff").replace("\u00a0", " ")
    zeilen = [zeile.rstrip() for zeile in io.StringIO(bereinigt).readlines()]

    # Leerzeilen an den Raendern entfernen; dazwischenliegende ueberspringt
    # der Importer selbst.
    while zeilen and not zeilen[0].strip():
        zeilen.pop(0)
    while zeilen and not zeilen[-1].strip():
        zeilen.pop()

    if not zeilen:
        return b""
    return ("\n".join(zeilen) + "\n").encode("utf-8")


def waehle_csv_quelle(
    datei_inhalt: bytes | None,
    datei_name: str | None,
    text_rohdaten: bytes | None,
) -> tuple[bytes, str, str | None]:
    """Entscheidet zwischen hochgeladener Datei und bestaetigtem Textinhalt.

    ``text_rohdaten`` ist der bereits mit :func:`text_zu_rohdaten`
    aufbereitete Inhalt des Textfelds - aufbereitet wird er erst, wenn der
    Nutzer die Eingabe bestaetigt hat, nicht bei jedem Tastendruck.

    Die Datei hat Vorrang: sie ist die bewusstere Handlung. Liegt beides
    vor, wird das gemeldet, statt stillschweigend eine der beiden Eingaben
    zu verwerfen.

    Returns:
        ``(rohdaten, herkunft, hinweis)``. ``rohdaten`` ist leer, wenn
        nichts vorliegt; ``hinweis`` ist gesetzt, wenn beide Wege belegt
        sind.
    """
    hat_text = bool(text_rohdaten)

    if datei_inhalt:
        hinweis = (
            "Datei und bestätigter Text liegen vor – die Datei wird verwendet."
            if hat_text
            else None
        )
        return datei_inhalt, (datei_name or "Hochgeladene Datei"), hinweis

    if hat_text:
        return bytes(text_rohdaten or b""), "Eingefügter CSV-Text", None

    return b"", "", None


def _letztes_datum(artikel: SKUInput) -> date | None:
    """Juengstes parsbares Datum der Historie."""
    daten = [parse_datum(satz.datum) for satz in artikel.historie]
    vorhanden = [d for d in daten if d is not None]
    return max(vorhanden) if vorhanden else None


def verlaufsrahmen(
    artikel: SKUInput, pfad: ForecastPfad
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Baut die Datenrahmen fuer Historie und Prognose.

    Der erste Prognosepunkt wird mit dem letzten Ist-Wert verknuepft, damit
    die Kurve im Graph nicht abreisst.

    Returns:
        ``(historie, prognose)`` - Spalten ``datum``/``menge`` bzw.
        ``datum``/``p10``/``p50``/``p90``.
    """
    saetze = []
    for satz in artikel.historie:
        geparst = parse_datum(satz.datum)
        saetze.append({"datum": geparst, "menge": float(satz.menge), "roh": satz.datum})

    historie = pd.DataFrame(saetze)
    if historie["datum"].notna().all():
        historie = historie.sort_values("datum").reset_index(drop=True)
    else:
        # Ohne parsbare Daten wird ersatzweise durchnummeriert, damit der
        # Graph trotzdem etwas zeigt.
        basis = date(2000, 1, 1)
        schritt = timedelta(days=int(pfad.periodenlaenge_tage))
        historie["datum"] = [basis + i * schritt for i in range(len(historie))]

    letztes = historie["datum"].iloc[-1]
    schritt = timedelta(days=max(1, int(round(pfad.periodenlaenge_tage))))

    zeilen = [
        {
            "datum": letztes,
            "p10": float(historie["menge"].iloc[-1]),
            "p50": float(historie["menge"].iloc[-1]),
            "p90": float(historie["menge"].iloc[-1]),
            "typ": "Anschluss",
        }
    ]
    for index in range(pfad.laenge):
        zeilen.append(
            {
                "datum": letztes + (index + 1) * schritt,
                "p10": float(pfad.p10[index]),
                "p50": float(pfad.p50[index]),
                "p90": float(pfad.p90[index]),
                "typ": "Prognose",
            }
        )

    return historie[["datum", "menge"]], pd.DataFrame(zeilen)


def _ergebnisrahmen(ergebnisse: list[AnalysisResult]) -> pd.DataFrame:
    """Wandelt die Prioritaetenliste in eine Anzeigetabelle."""
    return pd.DataFrame(
        [
            {
                "Status": e.status,
                "Artikel": e.sku,
                # Bewusst der Sentinel 999 statt inf: eine NumberColumn stellt
                # inf als leere Zelle dar, und leer ist mehrdeutig.
                "Reichweite (Tage)": e.reichweite_tage,
                "Tagesbedarf": e.prognose_tagesbedarf,
                "Meldebestand": e.meldebestand,
                "Nachbestellmenge": e.nachbestellmenge,
                "Sicherheitsbestand": e.sicherheitsbestand,
                "Empfehlung": e.empfohlene_massnahme,
            }
            for e in ergebnisse
        ]
    )


# ---------------------------------------------------------------------------
# Engine-Anbindung
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner="Prognosemodell wird geladen ...")
def _hole_engine():
    """Baut die Engine einmalig je Session.

    ``cache_resource`` ist hier wesentlich: das Laden des TimesFM-Modells
    dauert und darf nicht bei jedem Rerun erneut passieren.
    """
    config = lade_config()
    engine = baue_engine(config)
    fehler: str | None = None
    if config.force_timesfm:
        try:
            engine.starte()
        except ForecastUnavailable as exc:
            # Die Oberflaeche soll die Ursache zeigen statt abzustuerzen -
            # gerechnet wird trotzdem nicht mit einem Ersatzverfahren.
            fehler = str(exc)
    return engine, fehler


def _modellstatus(engine, ladefehler: str | None) -> None:
    """Zeigt Prognosemodell und Bezugsquelle in der Seitenleiste."""
    adapter = next(
        (a for a in engine.prognose_kette if isinstance(a, TimesFMForecaster)), None
    )

    if ladefehler:
        st.sidebar.error("Prognosemodell nicht verfügbar")
        st.sidebar.caption(ladefehler)
        st.sidebar.caption(
            "TimesFM ist Pflichtmodell (FORCE_TIMESFM). Ein Rückfall auf ein "
            "anderes Verfahren ist bewusst blockiert. Zum Ausprobieren ohne "
            "Modellgewichte: `FORCE_TIMESFM=false`."
        )
        return

    if engine.config.force_timesfm:
        st.sidebar.success("TimesFM aktiv · Fallback blockiert")
    else:
        st.sidebar.warning("Notbetrieb: statistischer Schätzer erlaubt")

    if adapter is not None:
        bezug = "lokal eingebacken" if adapter.laedt_lokal else "Hugging-Face-Hub"
        st.sidebar.caption(f"Quelle: `{adapter.quelle()}` ({bezug})")
    st.sidebar.caption(
        "Kette: " + ", ".join(a.name for a in engine.prognose_kette)
    )


# ---------------------------------------------------------------------------
# Zone 1 - Eingabe
# ---------------------------------------------------------------------------
def _zone_eingabe() -> tuple[list[SKUInput], dict[str, str], list, str]:
    """Seitenleiste: Datenquelle und Prognosehorizont.

    Returns:
        ``(artikel, spalten_mapping, warnungen, quelle)``
    """
    st.sidebar.header("1 · Eingabe")

    modus = st.sidebar.radio(
        "Datenquelle",
        ["Demo-Datensatz", "CSV-Upload"],
        help="Demo-Daten sind synthetisch erzeugt - im Projekt liegen keine Beispieldateien.",
    )

    artikel: list[SKUInput] = []
    mapping: dict[str, str] = {}
    warnungen: list = []
    quelle = ""

    if modus == "CSV-Upload":
        datei = st.sidebar.file_uploader(
            "ERP-Export (CSV)",
            type=["csv", "txt"],
            help=(
                "Spalten werden flexibel zugeordnet: Art-Nr, SKU, Bestand, "
                "Lager, Vorlaufzeit, ... Lang- und Breitformat werden erkannt."
            ),
        )

        # Zweiter Weg ohne Datei-Dialog: Auf Mobilgeraeten ist das Auswaehlen
        # einer Datei umstaendlich, Einfuegen aus der Zwischenablage nicht.
        eingefuegt = st.sidebar.text_area(
            "Oder CSV-Text direkt hier einfügen",
            height=150,
            placeholder="Artikel;Bestand;Lieferzeit;2024-01;2024-02;...",
            help=(
                "CSV-Inhalt kopieren, hier einsetzen und anschließend "
                "'Eingabe berechnen' drücken. Gleiche Spaltenerkennung wie "
                "beim Upload."
            ),
        )

        # Bewusst ein eigener Knopf: Auf Mobilgeraeten loest das Textfeld
        # beim Tippen und bei jedem Fokuswechsel einen Rerun aus. Ohne
        # Bestaetigung wuerde die App auf halb eingefuegtem Text rechnen und
        # den Nutzer mit Fehlermeldungen beschiessen, die sich von selbst
        # wieder erledigen.
        if st.sidebar.button("Eingabe berechnen", width="stretch"):
            st.session_state[SCHLUESSEL_TEXT] = text_zu_rohdaten(eingefuegt or "")
            if not st.session_state[SCHLUESSEL_TEXT]:
                st.sidebar.warning("Das Textfeld ist leer – es gibt nichts zu berechnen.")

        rohdaten, herkunft, hinweis = waehle_csv_quelle(
            datei.getvalue() if datei is not None else None,
            datei.name if datei is not None else None,
            st.session_state.get(SCHLUESSEL_TEXT, b""),
        )
        if hinweis:
            st.sidebar.info(hinweis)

        if rohdaten:
            try:
                ergebnis = lese_csv(rohdaten)
            except CSVIngestFehler as exc:
                st.sidebar.error(f"CSV nicht lesbar: {exc}")
            else:
                artikel = ergebnis.artikel
                mapping = ergebnis.spalten_mapping
                warnungen = ergebnis.warnungen
                quelle = f"{herkunft} ({ergebnis.layout}format)"
                st.sidebar.success(f"{len(artikel)} Artikel gelesen")
        elif eingefuegt and eingefuegt.strip():
            st.sidebar.info("Text erfasst – jetzt 'Eingabe berechnen' drücken.")
        else:
            st.sidebar.info("Noch keine Datei gewählt und kein Text eingefügt.")
    else:
        titel = {s.titel: s for s in DEMO_SZENARIEN}
        gewaehlt = st.sidebar.selectbox("Szenario", list(titel))
        szenario = titel[gewaehlt]
        st.sidebar.caption(szenario.beschreibung)

        monate = st.sidebar.slider(
            "Länge der Historie (Monate)", min_value=6, max_value=36, value=24, step=1
        )
        artikel = baue_szenario(szenario.schluessel, perioden=monate)
        quelle = f"Demo · {szenario.titel} · {monate} Monate"

        st.sidebar.download_button(
            "Als CSV herunterladen",
            data=als_csv(artikel),
            file_name=f"demo-{szenario.schluessel}.csv",
            mime="text/csv",
            help="Zeigt, wie ein passender ERP-Export aussehen muss.",
        )

    return artikel, mapping, warnungen, quelle


def _zone_horizont() -> float:
    """Schieberegler fuer den Prognosehorizont; liefert Tage."""
    st.sidebar.header("Prognosehorizont")
    einheit = st.sidebar.radio("Einheit", ["Monate", "Tage"], horizontal=True)

    if einheit == "Monate":
        monate = st.sidebar.slider(
            "Horizont (Monate)", min_value=1, max_value=24, value=6, step=1
        )
        tage = monate * 30.0
    else:
        tage = float(
            st.sidebar.slider(
                "Horizont (Tage)", min_value=7, max_value=720, value=180, step=7
            )
        )

    st.sidebar.caption(f"Entspricht {tage:g} Tagen.")
    return tage


# ---------------------------------------------------------------------------
# Zone 2 - Radar
# ---------------------------------------------------------------------------
def _radar_graph(historie: pd.DataFrame, prognose: pd.DataFrame) -> alt.LayerChart:
    """Baut den interaktiven Verlaufsgraphen."""
    korridor = (
        alt.Chart(prognose)
        .mark_area(opacity=0.28, color=FARBE_KORRIDOR)
        .encode(
            x=alt.X("datum:T", title="Periode"),
            y=alt.Y("p10:Q", title="Menge"),
            y2=alt.Y2("p90:Q"),
            tooltip=[
                alt.Tooltip("datum:T", title="Periode"),
                alt.Tooltip("p10:Q", title="P10", format=".1f"),
                alt.Tooltip("p50:Q", title="Prognose", format=".1f"),
                alt.Tooltip("p90:Q", title="P90", format=".1f"),
            ],
        )
    )

    prognoselinie = (
        alt.Chart(prognose)
        .mark_line(strokeDash=[6, 4], strokeWidth=2.5, color=FARBE_PROGNOSE)
        .encode(
            x="datum:T",
            y="p50:Q",
            tooltip=[
                alt.Tooltip("datum:T", title="Periode"),
                alt.Tooltip("p50:Q", title="Prognose", format=".1f"),
            ],
        )
    )

    ist = (
        alt.Chart(historie)
        .mark_line(strokeWidth=2.5, color=FARBE_HISTORIE, point=alt.OverlayMarkDef(size=28))
        .encode(
            x="datum:T",
            y=alt.Y("menge:Q", title="Menge"),
            tooltip=[
                alt.Tooltip("datum:T", title="Periode"),
                alt.Tooltip("menge:Q", title="Ist-Verbrauch", format=".1f"),
            ],
        )
    )

    return (
        alt.layer(korridor, prognoselinie, ist)
        .properties(height=380)
        .interactive()
    )


def _zone_radar(engine, artikel: list[SKUInput], horizont_tage: float) -> None:
    """Zeigt Verlauf und Prognose eines gewaehlten Artikels."""
    st.subheader("2 · Radar")

    if not artikel:
        st.info("Wähle links eine Datenquelle, um den Verlauf zu sehen.")
        return

    nach_sku = {a.sku: a for a in artikel}
    gewaehlt = st.selectbox("Artikel", list(nach_sku), key="radar_sku")
    ausgewaehlt = nach_sku[gewaehlt]

    serie = engine.baue_reihe(ausgewaehlt)
    perioden = horizont_in_perioden(horizont_tage, serie.periodenlaenge_tage)

    try:
        pfad = engine.prognose_pfad(ausgewaehlt, perioden)
    except ForecastUnavailable as exc:
        st.error(f"Keine Prognose möglich: {exc}")
        return

    historie, prognose = verlaufsrahmen(ausgewaehlt, pfad)
    st.altair_chart(_radar_graph(historie, prognose), width="stretch")

    kadenz = (
        "Tage" if serie.periodenlaenge_tage <= 1.5
        else "Wochen" if serie.periodenlaenge_tage <= 10
        else "Monate"
    )
    st.caption(
        f"Blau: Ist-Verbrauch ({serie.laenge} Perioden à {serie.periodenlaenge_tage:g} Tage, "
        f"Kadenz {kadenz}). Orange gestrichelt: Prognose über {pfad.laenge} Perioden. "
        f"Fläche: P10–P90-Korridor. Modell: {pfad.modell}."
    )


# ---------------------------------------------------------------------------
# Zone 3 - Entscheidung
# ---------------------------------------------------------------------------
def _zone_entscheidung(ergebnisse: list[AnalysisResult]) -> None:
    """Ampel-Tabelle mit Reichweite und Nachbestellmenge."""
    st.subheader("3 · Entscheidung")

    if not ergebnisse:
        st.info("Noch keine Analyse — wähle links eine Datenquelle.")
        return

    zaehler = {status.value: 0 for status in Status}
    for ergebnis in ergebnisse:
        zaehler[ergebnis.status] = zaehler.get(ergebnis.status, 0) + 1
    gesamt = round(sum(e.nachbestellmenge for e in ergebnisse), 2)

    spalten = st.columns(4)
    spalten[0].metric("🔴 Kritisch", zaehler.get(Status.KRITISCH.value, 0))
    spalten[1].metric("🟢 Optimal", zaehler.get(Status.OPTIMAL.value, 0))
    spalten[2].metric("🟡 Überbestand", zaehler.get(Status.UEBERBESTAND.value, 0))
    spalten[3].metric("Σ Nachbestellmenge", f"{gesamt:g}")

    filter_auswahl = st.multiselect(
        "Status filtern",
        [status.value for status in Status],
        default=[status.value for status in Status],
    )
    gefiltert = [e for e in ergebnisse if e.status in filter_auswahl]
    if not gefiltert:
        st.warning("Kein Artikel passt zum Filter.")
        return

    rahmen = _ergebnisrahmen(gefiltert)
    st.dataframe(
        rahmen.style.map(
            lambda wert: AMPEL_HINTERGRUND.get(wert, ""), subset=["Status"]
        ),
        width="stretch",
        hide_index=True,
        column_config={
            "Reichweite (Tage)": st.column_config.NumberColumn(
                format="%.1f",
                help=f"{KEINE_REICHWEITE:g} = kein Verbrauch prognostiziert",
            ),
            "Tagesbedarf": st.column_config.NumberColumn(format="%.2f"),
            "Meldebestand": st.column_config.NumberColumn(format="%.2f"),
            "Nachbestellmenge": st.column_config.NumberColumn(format="%.2f"),
            "Sicherheitsbestand": st.column_config.NumberColumn(format="%.2f"),
            "Empfehlung": st.column_config.TextColumn(width="large"),
        },
    )
    st.caption(
        "Sortiert als Prioritätenliste: kritische Artikel mit der geringsten "
        "Reichweite zuerst."
    )

    st.download_button(
        "Prioritätenliste als CSV",
        data=rahmen.to_csv(index=False, sep=";"),
        file_name="prioritaetenliste.csv",
        mime="text/csv",
    )


# ---------------------------------------------------------------------------
# Einstiegspunkt
# ---------------------------------------------------------------------------
def main() -> None:
    """Baut die gesamte Oberflaeche auf."""
    st.set_page_config(page_title=TITEL, page_icon="📦", layout="wide")
    st.title(f"📦 {TITEL}")
    st.caption(
        "Dispositions-Radar: Bedarfsprognose, Meldebestand und "
        "Nachbestellmenge je Artikel."
    )

    engine, ladefehler = _hole_engine()
    _modellstatus(engine, ladefehler)

    artikel, mapping, warnungen, quelle = _zone_eingabe()
    horizont_tage = _zone_horizont()

    if quelle:
        st.caption(f"Datenquelle: **{quelle}** · {len(artikel)} Artikel")
    if mapping:
        with st.expander("Erkannte Spaltenzuordnung"):
            st.json(mapping)
    if warnungen:
        with st.expander(f"{len(warnungen)} Hinweis(e) aus dem Import"):
            for warnung in warnungen:
                st.write(f"- {warnung.sku or f'Zeile {warnung.zeile}'}: {warnung.meldung}")

    ergebnisse: list[AnalysisResult] = []
    if artikel:
        try:
            ergebnisse = engine.analysiere_batch(artikel)
        except ForecastUnavailable as exc:
            st.error(
                f"Analyse nicht möglich: {exc}\n\n"
                "TimesFM ist Pflichtmodell — es wird bewusst nicht mit einem "
                "Ersatzverfahren weitergerechnet."
            )

    radar, entscheidung = st.columns([3, 2], gap="large")
    with radar:
        _zone_radar(engine, artikel, horizont_tage)
    with entscheidung:
        if ergebnisse:
            oberste = ergebnisse[0]
            st.subheader("Dringendster Artikel")
            st.metric(
                oberste.sku,
                oberste.status,
                f"{oberste.reichweite_tage:g} Tage Reichweite",
                # Neutral einfaerben: die Reichweite ist kein Delta, und ein
                # gruener Aufwaertspfeil neben "KRITISCH" liest sich falsch.
                delta_color="off",
            )
            st.write(oberste.empfohlene_massnahme)

    st.divider()
    _zone_entscheidung(ergebnisse)


if __name__ == "__main__":
    main()
