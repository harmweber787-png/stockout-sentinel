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

import base64
import io
import math
from datetime import date, timedelta
from html import escape

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
    "marke_svg",
    "marke_datauri",
    "marken_header",
    "sentinel_css",
    "text_zu_rohdaten",
    "waehle_csv_quelle",
]

#: Kurzform fuer Browser-Tab und Lesezeichen.
TITEL = "Sentinel B2B"

#: Vollstaendige Marke im Seitenkopf.
TITEL_LANG = "Sentinel B2B · Bestands- & Dispositions-Radar"

#: Schluessel im Session-State: der zuletzt *bestaetigte* Textfeldinhalt.
#: Ohne diese Ablage waere der Inhalt nach dem naechsten Rerun - etwa beim
#: Verschieben des Horizont-Reglers - wieder verloren, denn ein Button
#: meldet seinen Druck nur im unmittelbar folgenden Durchlauf.
SCHLUESSEL_TEXT = "bestaetigter_csv_text"

# ---------------------------------------------------------------------------
# Marke
# ---------------------------------------------------------------------------
#: Ampelfarben der Marke. Einmal hier, dann ueberall referenziert - Farbwerte
#: verstreut im Code laufen sonst auseinander.
AMPEL_FARBE = {
    "KRITISCH": "#E11D48",
    "UEBERBESTAND": "#F59E0B",
    "OPTIMAL": "#10B981",
}

def marke_svg(schildfarbe: str = "#475569") -> str:
    """Signet der Marke: Schild mit Radarbogen und Peilpunkt.

    Args:
        schildfarbe: Konturfarbe des Schilds (Slate). Die Radarbögen bleiben
            in Emerald, damit die Marke in beiden Themes wiedererkennbar ist.
    """
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
        '<path d="M24 4.5 40.5 10.2v13.3c0 9.6-6.6 16.7-16.5 19.9'
        'C14.1 40.2 7.5 33.1 7.5 23.5V10.2Z" fill="none" '
        f'stroke="{schildfarbe}" stroke-width="2.6" stroke-linejoin="round"/>'
        '<path d="M15.5 25.5a8.5 8.5 0 0 1 8.5-8.5" fill="none" '
        f'stroke="{AMPEL_FARBE["OPTIMAL"]}" stroke-width="2.4" stroke-linecap="round"/>'
        '<path d="M19.8 27.6a4.2 4.2 0 0 1 4.2-4.2" fill="none" '
        f'stroke="{AMPEL_FARBE["OPTIMAL"]}" stroke-width="2.4" '
        'stroke-linecap="round" opacity="0.75"/>'
        f'<circle cx="24" cy="31.5" r="2.6" fill="{AMPEL_FARBE["OPTIMAL"]}"/>'
        "</svg>"
    )


def marke_datauri(schildfarbe: str = "#475569") -> str:
    """Verpackt das Signet als Base64-Data-URI fuer CSS.

    Der Umweg ueber CSS ist noetig: ``st.html`` entfernt ``<svg>`` beim
    Bereinigen des Markups, ein ``<style>``-Block kommt dagegen unveraendert
    durch. Base64 statt URL-Kodierung, damit weder Rauten noch
    Anfuehrungszeichen der Farbwerte das ``url()`` zerlegen.
    """
    kodiert = base64.b64encode(marke_svg(schildfarbe).encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{kodiert}"


def kopfdaten_skript() -> str:
    """Traegt Apple-Touch-Icon und Home-Screen-Namen in den Dokumentenkopf ein.

    ``st.html`` scheidet dafuer aus: Es rendert in den Body und entfernt
    ``<link>`` und ``<script>`` beim Bereinigen - im Browser geprueft, es
    landete kein einziger Link im DOM. ``st.iframe`` bettet dagegen in einem
    gleichnamigen Rahmen ein, der ueber ``window.parent`` an den Kopf des
    Hauptdokuments darf.

    Der Eintrag ist idempotent: Streamlit fuehrt das Skript bei jedem Rerun
    erneut aus, ohne die Pruefung saeße der Link bald hundertfach im Kopf.
    """
    return f"""
<script>
(function () {{
  var kopf = window.parent.document.head;
  if (!kopf) return;

  function setze(selektor, bauen) {{
    var vorhanden = kopf.querySelector(selektor);
    if (vorhanden) vorhanden.remove();
    kopf.appendChild(bauen());
  }}

  // Signet fuer "Zum Home-Bildschirm".
  setze('link[rel="apple-touch-icon"]', function () {{
    var l = window.parent.document.createElement("link");
    l.rel = "apple-touch-icon";
    l.href = "{marke_datauri()}";
    return l;
  }});

  // Safari schlaegt diesen Namen beim Ablegen vor.
  setze('meta[name="apple-mobile-web-app-title"]', function () {{
    var m = window.parent.document.createElement("meta");
    m.name = "apple-mobile-web-app-title";
    m.content = "{TITEL}";
    return m;
  }});

  window.parent.document.title = "{TITEL}";
}})();
</script>
"""


def marken_header() -> str:
    """Baut den Seitenkopf: Signet (per CSS) neben der Wortmarke."""
    return (
        '<header class="sentinel-kopf">'
        '<span class="sentinel-marke" role="img" aria-label="Sentinel B2B"></span>'
        '<div class="sentinel-kopf__text">'
        f'<h1 class="sentinel-titel">{TITEL_LANG}</h1>'
        '<p class="sentinel-claim">Bedarfsprognose, Meldebestand und '
        'Nachbestellmenge je Artikel &ndash; priorisiert nach Dringlichkeit.</p>'
        "</div></header>"
    )


def sentinel_css() -> str:
    """Liefert das Stylesheet der Oberflaeche.

    Selektiert ueber eigene Klassen (``sentinel-*``) und ueber Streamlits
    ``data-testid``-Attribute. Letztere sind Streamlits zugesicherter
    Test-Hook und damit stabil - anders als die generierten Hash-Klassen
    der Styling-Engine, die sich mit jeder Version aendern koennen.
    """
    marke_hell = marke_datauri("#475569")
    marke_dunkel = marke_datauri("#94a3b8")
    return f"""
<style>
:root {{
  --sentinel-flaeche: #ffffff;
  --sentinel-rand: #e2e8f0;
  --sentinel-text: #0f172a;
  --sentinel-gedaempft: #64748b;
  --sentinel-schatten: 0 1px 2px rgba(15,23,42,.05), 0 6px 16px rgba(15,23,42,.06);
  --ampel-kritisch: {AMPEL_FARBE["KRITISCH"]};
  --ampel-ueberbestand: {AMPEL_FARBE["UEBERBESTAND"]};
  --ampel-optimal: {AMPEL_FARBE["OPTIMAL"]};
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --sentinel-flaeche: #111826;
    --sentinel-rand: #1f2a3a;
    --sentinel-text: #e2e8f0;
    --sentinel-gedaempft: #94a3b8;
    --sentinel-schatten: 0 1px 2px rgba(0,0,0,.3), 0 6px 16px rgba(0,0,0,.25);
  }}
}}

/* --- Seitenkopf ------------------------------------------------------- */
.sentinel-kopf {{
  display: flex; align-items: center; gap: .85rem;
  margin: 0 0 .35rem 0; color: var(--sentinel-text);
}}
.sentinel-marke {{
  width: 44px; height: 44px; flex: 0 0 44px; display: block;
  background-image: url("{marke_hell}");
  background-size: contain; background-repeat: no-repeat;
  background-position: center;
}}
@media (prefers-color-scheme: dark) {{
  .sentinel-marke {{ background-image: url("{marke_dunkel}"); }}
}}
.sentinel-kopf__text {{ min-width: 0; }}
.sentinel-titel {{
  margin: 0; font-weight: 700; letter-spacing: -.02em; line-height: 1.15;
  /* Skaliert mit der Breite, statt auf dem Handy umzubrechen oder zu clippen. */
  font-size: clamp(1.25rem, 4.2vw, 2rem);
}}
.sentinel-claim {{
  margin: .15rem 0 0 0; color: var(--sentinel-gedaempft);
  font-size: clamp(.8rem, 2.4vw, .95rem); line-height: 1.35;
}}

/* --- Bento-Karten fuer die Kennzahlen --------------------------------- */
[data-testid="stMetric"] {{
  background: var(--sentinel-flaeche);
  border: 1px solid var(--sentinel-rand);
  border-radius: 12px;
  padding: .85rem 1rem .95rem 1rem;
  box-shadow: var(--sentinel-schatten);
  /* Karten einer Reihe gleich hoch, auch bei unterschiedlich langen Labels. */
  height: 100%;
}}

/* Layout-Fehler auf schmalen Schirmen: Streamlit kuerzt den Kennzahlenwert
   mit Ellipse, sodass aus "646.5" ein abgeschnittener Rest wurde. Wert und
   Beschriftung duerfen umbrechen statt zu clippen. */
[data-testid="stMetricValue"] {{
  font-size: clamp(1.1rem, 4.6vw, 1.85rem) !important;
  line-height: 1.2 !important;
  overflow: visible !important;
  text-overflow: clip !important;
  white-space: normal !important;
  overflow-wrap: anywhere;
}}
[data-testid="stMetricValue"] > div {{
  overflow: visible !important;
  text-overflow: clip !important;
  white-space: normal !important;
}}
[data-testid="stMetricLabel"],
[data-testid="stMetricLabel"] p {{
  overflow: visible !important;
  text-overflow: clip !important;
  white-space: normal !important;
  font-size: clamp(.72rem, 2.5vw, .85rem) !important;
  color: var(--sentinel-gedaempft) !important;
}}

/* --- Status-Badges ----------------------------------------------------- */
.sentinel-badge {{
  display: inline-flex; align-items: center; gap: .4rem;
  padding: .28rem .7rem; border-radius: 999px;
  font-weight: 600; font-size: .82rem; letter-spacing: .01em;
  border: 1px solid currentColor;
}}
.sentinel-badge--kritisch {{
  color: var(--ampel-kritisch); background: color-mix(in srgb, var(--ampel-kritisch) 12%, transparent);
}}
.sentinel-badge--ueberbestand {{
  color: var(--ampel-ueberbestand); background: color-mix(in srgb, var(--ampel-ueberbestand) 14%, transparent);
}}
.sentinel-badge--optimal {{
  color: var(--ampel-optimal); background: color-mix(in srgb, var(--ampel-optimal) 12%, transparent);
}}

/* --- Fokuskarte (dringendster Artikel) --------------------------------- */
.sentinel-karte {{
  background: var(--sentinel-flaeche);
  border: 1px solid var(--sentinel-rand);
  border-radius: 12px; padding: 1rem 1.1rem;
  box-shadow: var(--sentinel-schatten); color: var(--sentinel-text);
}}
.sentinel-karte__sku {{
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: .95rem; overflow-wrap: anywhere; margin-bottom: .5rem;
}}
.sentinel-karte__kennzahl {{
  margin-top: .7rem; font-size: clamp(1.3rem, 5vw, 1.9rem);
  font-weight: 700; line-height: 1.15;
}}
.sentinel-karte__einheit {{
  font-size: .8rem; font-weight: 500; color: var(--sentinel-gedaempft);
  margin-left: .35rem;
}}
.sentinel-karte__text {{
  margin: .6rem 0 0 0; color: var(--sentinel-gedaempft);
  font-size: .88rem; line-height: 1.45;
}}

/* --- Prioritaetentabelle ---------------------------------------------- */
[data-testid="stDataFrame"] {{
  border: 1px solid var(--sentinel-rand);
  border-radius: 12px; overflow: hidden;
  box-shadow: var(--sentinel-schatten);
}}
/* Waagerechtes Wischen auf Touch-Geraeten mit Schwung statt ruckelnd. */
[data-testid="stDataFrame"] * {{ -webkit-overflow-scrolling: touch; }}

/* --- Schnelleinstieg --------------------------------------------------- */
.sentinel-schritt {{ display: flex; gap: .7rem; margin: 0 0 .7rem 0; }}
.sentinel-schritt__nr {{
  flex: 0 0 1.6rem; height: 1.6rem; border-radius: 50%;
  background: var(--ampel-optimal); color: #fff;
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: .82rem;
}}
.sentinel-schritt__text {{ line-height: 1.45; }}
.sentinel-schritt__text b {{ color: var(--sentinel-text); }}

/* --- Schmale Schirme --------------------------------------------------- */
@media (max-width: 640px) {{
  [data-testid="stMetric"] {{ padding: .7rem .8rem .8rem .8rem; }}
  .sentinel-marke {{ width: 34px; height: 34px; flex: 0 0 34px; }}
  .sentinel-kopf {{ gap: .6rem; }}
}}
</style>
"""

#: Farbwahl bewusst kontrastreich und nicht rot/gruen-abhaengig - die Ampel
#: traegt ihre Aussage ohnehin im Symbol, der Graph soll unabhaengig davon
#: lesbar bleiben.
FARBE_HISTORIE = "#1f3a5f"
FARBE_PROGNOSE = "#d97706"
FARBE_KORRIDOR = "#fbbf24"

#: Ampelfarben fuer die Hinterlegung der Entscheidungstabelle.
#: Hinterlegung der Statuszelle. Farbton aus AMPEL_FARBE, stark
#: abgeschwaecht - die Zelle soll den Blick lenken, nicht ueberstrahlen.
AMPEL_HINTERGRUND = {
    Status.KRITISCH.value: (
        f"background-color: {AMPEL_FARBE['KRITISCH']}22; "
        f"color: {AMPEL_FARBE['KRITISCH']}; font-weight: 600"
    ),
    Status.OPTIMAL.value: (
        f"background-color: {AMPEL_FARBE['OPTIMAL']}22; "
        f"color: {AMPEL_FARBE['OPTIMAL']}; font-weight: 600"
    ),
    Status.UEBERBESTAND.value: (
        f"background-color: {AMPEL_FARBE['UEBERBESTAND']}26; "
        f"color: {AMPEL_FARBE['UEBERBESTAND']}; font-weight: 600"
    ),
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


#: Erklaertexte der Fachbegriffe. Einmal definiert, dann in Tabelle und
#: Kennzahlen gleichlautend verwendet - widerspruechliche Erklaerungen an
#: zwei Stellen sind schlimmer als gar keine.
ERKLAERUNG = {
    "meldebestand": (
        "Lagerbestand, bei dessen Erreichen sofort bestellt werden muss, "
        "um die Lieferzeit abzudecken."
    ),
    "sicherheitsbestand": (
        "Pufferreserve gegen unvorhersehbare Absatzspitzen oder "
        "Lieferverzögerungen."
    ),
    "reichweite": (
        "Verbleibende Tage, bis der aktuelle Lagerbestand bei "
        "prognostiziertem Verbrauch auf null sinkt."
    ),
    "tagesbedarf": (
        "Prognostizierter Verbrauch pro Tag – Grundlage für Reichweite "
        "und Meldebestand."
    ),
    "nachbestellmenge": (
        "Empfohlene Bestellmenge, um die Lücke bis zum Meldebestand zu "
        "schließen. 0 bedeutet: keine Bestellung nötig."
    ),
}


#: Zwei Musterzeilen im Breitformat. Keine Fachdaten - eine Formatvorlage,
#: an der sich die Spaltenstruktur ablesen laesst.
MUSTER_BREIT = (
    "Artikel;Bestand;Lieferzeit;2024-01;2024-02;2024-03\n"
    "ARTIKEL-001;120;30;55;61;58\n"
    "ARTIKEL-002;40;60;12;9;7\n"
)

#: Dieselben zwei Artikel im Langformat.
MUSTER_LANG = (
    "Artikel;Datum;Menge;Bestand;Lieferzeit\n"
    "ARTIKEL-001;2024-01;55;120;30\n"
    "ARTIKEL-001;2024-02;61;120;30\n"
)


def _schnelleinstieg() -> None:
    """Einklappbare Kurzanleitung direkt unter dem Seitenkopf.

    Eingeklappt voreingestellt: Wer den Ablauf kennt, soll nicht jedes Mal
    daran vorbeiscrollen muessen.
    """
    schritte = (
        (
            "Verbrauchsdaten einfügen",
            "CSV-Export aus dem Warenwirtschaftssystem hochladen – oder den "
            "Inhalt links ins Textfeld einsetzen und „Eingabe berechnen“ "
            "drücken. Welche vier Angaben die Datei enthalten muss, steht "
            "unter der Schrittfolge.",
        ),
        (
            "KI-Analyse abwarten",
            "TimesFM prognostiziert je Artikel den künftigen Bedarf und "
            "spannt einen Korridor zwischen bestem und schlechtestem Fall "
            "(P10–P90) auf. Daraus entstehen Melde- und Sicherheitsbestand.",
        ),
        (
            "Aktion ausführen",
            "Die Prioritätenliste steht nach Dringlichkeit sortiert: knappste "
            "Reichweite zuerst. Von oben abarbeiten und die "
            "Nachbestellmengen als CSV exportieren.",
        ),
    )

    with st.expander("💡 Schnelleinstieg: Disposition in 3 Schritten"):
        st.html(
            "".join(
                f'<div class="sentinel-schritt">'
                f'<div class="sentinel-schritt__nr">{nummer}</div>'
                f'<div class="sentinel-schritt__text"><b>{titel}</b><br>{text}</div>'
                f"</div>"
                for nummer, (titel, text) in enumerate(schritte, start=1)
            )
        )

        st.markdown("#### Diese vier Angaben braucht die Datei")
        st.markdown(
            """
| Pflichtfeld | Erkannte Spaltennamen (Auswahl) | Bedeutung |
| --- | --- | --- |
| **Artikel-ID** | `Artikel`, `Art-Nr`, `SKU`, `Artikelnummer`, `Material` | Eindeutige Kennung je Artikel |
| **Aktueller Lagerbestand** | `Bestand`, `Lager`, `Lagerbestand`, `Stock` | Menge, die heute im Lager liegt |
| **Lieferzeit (Tage)** | `Lieferzeit`, `Vorlaufzeit`, `WBZ`, `Lead Time` | Wiederbeschaffungszeit in Tagen |
| **Historische Absätze** | siehe Formate unten | **Mindestens 2 Perioden** je Artikel |

Groß-/Kleinschreibung, Trenn- und Sonderzeichen spielen keine Rolle:
`Art.-Nr.`, `ART NR` und `Art-Nr` gelten als dasselbe Feld.
"""
        )

        st.markdown("#### Zwei zulässige Aufbauten")
        breit, lang = st.tabs(["Breitformat (empfohlen)", "Langformat"])

        with breit:
            st.markdown(
                "Eine Zeile je Artikel, die Perioden stehen als **Datumsspalten** "
                "nebeneinander. Kompakt – gut zum Einfügen auf dem Handy."
            )
            st.markdown(
                """
| Artikel | Bestand | Lieferzeit | 2024-01 | 2024-02 | 2024-03 |
| --- | --- | --- | --- | --- | --- |
| ARTIKEL-001 | 120 | 30 | 55 | 61 | 58 |
| ARTIKEL-002 | 40 | 60 | 12 | 9 | 7 |
"""
            )
            st.code(MUSTER_BREIT, language="csv")

        with lang:
            st.markdown(
                "Eine Zeile je Artikel **und** Periode, mit eigenen Spalten für "
                "Datum und Menge. So exportieren die meisten ERP-Systeme."
            )
            st.markdown(
                """
| Artikel | Datum | Menge | Bestand | Lieferzeit |
| --- | --- | --- | --- | --- |
| ARTIKEL-001 | 2024-01 | 55 | 120 | 30 |
| ARTIKEL-001 | 2024-02 | 61 | 120 | 30 |
"""
            )
            st.code(MUSTER_LANG, language="csv")

        st.caption(
            "Datumsangaben in jedem gängigen Format: `2024-03-01`, "
            "`01.03.2024`, `2024-03` oder `Mrz 24`. Zahlen dürfen Schweizer "
            "oder deutsche Schreibweise tragen (`1'234.50`, `1.234,50`)."
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
            # Bewusst ohne Typenfilter: Die "Dateien"-App unter iOS reicht
            # Tabellen je nach Herkunft als text/plain oder ganz ohne Endung
            # weiter, und eine Endungsliste graut sie dann aus. Was wirklich
            # brauchbar ist, entscheidet ohnehin erst der Importer - und der
            # meldet unlesbare Inhalte mit klarem Text.
            type=None,
            help=(
                "Jedes textbasierte Tabellenformat (CSV, TXT, TSV). Spalten "
                "werden flexibel zugeordnet: Art-Nr, SKU, Bestand, Lager, "
                "Vorlaufzeit, ... Lang- und Breitformat werden erkannt."
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


def _fokuskarte(ergebnis: AnalysisResult) -> str:
    """Baut die Karte zum dringendsten Artikel als HTML.

    Bewusst keine ``st.metric``: Dort erschien die Reichweite als Delta mit
    Pfeil, was neben "KRITISCH" in die Irre fuehrte. Die Karte zeigt den
    Status als Badge in der Ampelfarbe und die Reichweite als das, was sie
    ist - eine Kennzahl, keine Veraenderung.
    """
    variante = {
        "KRITISCH": "kritisch",
        "OPTIMAL": "optimal",
        "UEBERBESTAND": "ueberbestand",
    }.get(ergebnis.status_code, "optimal")

    reichweite = (
        f"über {KEINE_REICHWEITE:g}"
        if ergebnis.reichweite_tage >= KEINE_REICHWEITE
        else f"{ergebnis.reichweite_tage:g}"
    )

    return (
        '<div class="sentinel-karte">'
        f'<div class="sentinel-karte__sku">{escape(ergebnis.sku)}</div>'
        f'<span class="sentinel-badge sentinel-badge--{variante}">'
        f"{escape(ergebnis.status)}</span>"
        f'<div class="sentinel-karte__kennzahl">{reichweite}'
        '<span class="sentinel-karte__einheit">Tage Reichweite</span></div>'
        f'<p class="sentinel-karte__text">'
        f"{escape(ergebnis.empfohlene_massnahme)}</p>"
        "</div>"
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
    spalten[0].metric(
        "🔴 Kritisch",
        zaehler.get(Status.KRITISCH.value, 0),
        help="Reichweite reicht nicht bis zur nächsten Lieferung – sofort handeln.",
    )
    spalten[1].metric(
        "🟢 Optimal",
        zaehler.get(Status.OPTIMAL.value, 0),
        help="Bestand im Zielkorridor zwischen einfacher und doppelter Lieferzeit.",
    )
    spalten[2].metric(
        "🟡 Überbestand",
        zaehler.get(Status.UEBERBESTAND.value, 0),
        help="Reichweite über der doppelten Lieferzeit – Kapital liegt im Lager.",
    )
    spalten[3].metric(
        "Σ Nachbestellmenge",
        f"{gesamt:g}",
        help=ERKLAERUNG["nachbestellmenge"],
    )

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
        # Hoehere Zeilen sind auf Touch-Geraeten deutlich leichter zu treffen.
        row_height=44,
        column_config={
            "Reichweite (Tage)": st.column_config.NumberColumn(
                format="%.1f",
                help=(
                    f"{ERKLAERUNG['reichweite']} "
                    f"{KEINE_REICHWEITE:g} = kein Verbrauch prognostiziert."
                ),
            ),
            "Tagesbedarf": st.column_config.NumberColumn(
                format="%.2f", help=ERKLAERUNG["tagesbedarf"]
            ),
            "Meldebestand": st.column_config.NumberColumn(
                format="%.2f", help=ERKLAERUNG["meldebestand"]
            ),
            "Nachbestellmenge": st.column_config.NumberColumn(
                format="%.2f", help=ERKLAERUNG["nachbestellmenge"]
            ),
            "Sicherheitsbestand": st.column_config.NumberColumn(
                format="%.2f", help=ERKLAERUNG["sicherheitsbestand"]
            ),
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
    st.set_page_config(page_title=TITEL, page_icon="📊", layout="wide")

    # Stylesheet zuerst, damit der Kopf schon gestaltet erscheint.
    st.html(sentinel_css())
    # Unsichtbarer Rahmen: traegt Signet und Home-Screen-Namen in den
    # Dokumentenkopf ein - ueber st.html ginge das nicht, weil dort <link>
    # und <script> beim Bereinigen entfernt werden. Der eingebettete Inhalt
    # ist ein festes Literal aus eigenen Konstanten, keine Fremdeingabe.
    # height=1: st.iframe verlangt eine positive Hoehe, und ein per
    # display:none versteckter Rahmen fuehrt je nach Browser dazu, dass
    # das Skript gar nicht erst laeuft.
    st.iframe(kopfdaten_skript(), height=1)
    st.html(marken_header())
    _schnelleinstieg()

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
            st.subheader("Dringendster Artikel")
            st.html(_fokuskarte(ergebnisse[0]))

    st.divider()
    _zone_entscheidung(ergebnisse)


if __name__ == "__main__":
    main()
