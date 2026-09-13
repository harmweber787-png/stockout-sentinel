"""Adapter: robuster statistischer Schaetzer (Trend + Sicherheitskorridor).

Dieser Adapter ist der garantiert verfuegbare Fallback der Prognosekette.
Er benoetigt keine Modellgewichte, kein Netzwerk und keinen Beschleuniger
und liefert deshalb auch dann ein belastbares Ergebnis, wenn das
Foundation-Model (TimesFM) nicht geladen werden kann.

Verfahren:
    1. Trend    - Theil-Sen-Regression (Median aller paarweisen Steigungen).
       Sie ist gegenueber Ausreissern unempfindlich und bleibt zugleich
       vorwaertsgerichtet, im Gegensatz zu einer nachlaufenden Glaettung.
    2. Extrapolation - die Fortschreibung um eine Periode wird gedaempft;
       die Daempfung waechst mit der Laenge der Historie, damit sehr kurze
       Reihen keinen steilen Trend in die Zukunft schreiben.
    3. Streuung - robuste Skala ueber die Median-Absolutabweichung (MAD)
       der Residuen, abgesichert durch einen Mindest-Variationskoeffizienten.
    4. Korridor - P90 = P50 + z(0.90) * Streuung.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from src.ports.forecasting import ConsumptionSeries, DemandForecast, ForecastPfad

__all__ = ["StatisticalForecaster", "Z_P90"]


#: Standardnormal-Quantil fuer 90 % Servicegrad.
Z_P90 = 1.2815515655446004

#: Skalierungsfaktor MAD -> Standardabweichung bei Normalverteilung.
_MAD_ZU_SIGMA = 1.4826


@dataclass(slots=True)
class StatisticalForecaster:
    """Trendbasierte Punktprognose mit robustem Sicherheitskorridor.

    Attributes:
        daempfung: Maximaler Anteil der Steigung, der ueber die letzte
            beobachtete Periode hinaus fortgeschrieben wird.
        max_fit_perioden: Obergrenze der fuer die Regression genutzten
            juengsten Perioden. Begrenzt die quadratische Laufzeit der
            Theil-Sen-Schaetzung und gewichtet aktuelle Nachfrage hoeher.
        min_variationskoeffizient: Untergrenze der relativen Streuung. Ohne
            sie faellt der Sicherheitsbestand bei perfekt konstanter
            Historie auf 0 - operativ unerwuenscht, da reale Nachfrage nie
            streuungsfrei ist.
    """

    daempfung: float = 0.7
    max_fit_perioden: int = 120
    min_variationskoeffizient: float = 0.10
    name: str = "statistical-theil-sen-p90"

    def verfuegbar(self) -> bool:
        """Der statistische Schaetzer ist immer einsatzbereit."""
        return True

    def prognose(self, serie: ConsumptionSeries) -> DemandForecast:
        werte = np.asarray(serie.werte, dtype=float)[-self.max_fit_perioden :]
        niveau, streuung = self._schaetze(werte)

        p50_periode = max(0.0, niveau)
        p90_periode = p50_periode + Z_P90 * streuung
        p10_periode = max(0.0, p50_periode - Z_P90 * streuung)

        # Normierung der Periodenprognose auf einen 30-Tage-Monat.
        faktor = serie.perioden_pro_monat
        return DemandForecast(
            monatsabsatz_p50=p50_periode * faktor,
            monatsabsatz_p90=p90_periode * faktor,
            monatsabsatz_p10=p10_periode * faktor,
            modell=self.name,
        )

    def prognose_pfad(self, serie: ConsumptionSeries, perioden: int) -> ForecastPfad:
        """Schreibt Trend und Korridor ueber mehrere Perioden fort.

        Der Korridor weitet sich mit dem Horizont: je weiter die Gerade
        extrapoliert wird, desto staerker schlaegt die Unsicherheit der
        geschaetzten Steigung durch. Der Faktor ``sqrt(1 + h/n)`` bildet das
        ab, ohne - wie eine Random-Walk-Annahme - unrealistisch schnell
        auszufransen.
        """
        if perioden < 1:
            raise ValueError("Der Prognosehorizont muss mindestens 1 Periode betragen.")

        werte = np.asarray(serie.werte, dtype=float)[-self.max_fit_perioden :]
        n = werte.size
        steigung, achsenabschnitt, streuung = self._pfad_parameter(werte)

        p10: list[float] = []
        p50: list[float] = []
        p90: list[float] = []
        for schritt in range(1, perioden + 1):
            daempfung = self.daempfung * min(1.0, (n - 1) / 4.0)
            index = (n - 1) + daempfung * schritt
            niveau = max(0.0, achsenabschnitt + steigung * index)

            aufweitung = float(np.sqrt(1.0 + schritt / max(n, 1)))
            spanne = Z_P90 * streuung * aufweitung

            p50.append(niveau)
            p90.append(niveau + spanne)
            p10.append(max(0.0, niveau - spanne))

        return ForecastPfad(
            p10=tuple(p10),
            p50=tuple(p50),
            p90=tuple(p90),
            periodenlaenge_tage=serie.periodenlaenge_tage,
            modell=self.name,
        )

    def _pfad_parameter(self, werte: np.ndarray) -> tuple[float, float, float]:
        """Liefert ``(steigung, achsenabschnitt, streuung)`` der Trendgeraden."""
        n = werte.size
        x = np.arange(n, dtype=float)
        steigung = _theil_sen_steigung(x, werte)
        achsenabschnitt = float(np.median(werte - steigung * x))
        residuen = werte - (achsenabschnitt + steigung * x)
        niveau = achsenabschnitt + steigung * (n - 1)
        return steigung, achsenabschnitt, self._robuste_streuung(residuen, werte, niveau)

    def _schaetze(self, werte: np.ndarray) -> tuple[float, float]:
        """Liefert ``(niveau, streuung)`` fuer die naechste Periode."""
        n = werte.size
        x = np.arange(n, dtype=float)

        steigung = _theil_sen_steigung(x, werte)
        achsenabschnitt = float(np.median(werte - steigung * x))
        residuen = werte - (achsenabschnitt + steigung * x)

        # Fortschreibung auf die naechste Periode (Index n), gedaempft.
        daempfung = self.daempfung * min(1.0, (n - 1) / 4.0)
        niveau = achsenabschnitt + steigung * (n - 1) + daempfung * steigung

        return float(niveau), self._robuste_streuung(residuen, werte, niveau)

    def _robuste_streuung(
        self, residuen: np.ndarray, werte: np.ndarray, niveau: float
    ) -> float:
        """MAD-basierte Streuung mit Mindest-Variationskoeffizient.

        Bewusst *nicht* die Standardabweichung: ein einzelner Ausreisser in
        der Historie wuerde den Sicherheitskorridor sonst um Groessen-
        ordnungen aufblaehen und die Disposition unbrauchbar machen.
        """
        mad = float(np.median(np.abs(residuen - np.median(residuen))))
        sigma = mad * _MAD_ZU_SIGMA

        if werte.size == 2:
            # Bei zwei Perioden liegt die Regressionsgerade exakt auf den
            # Punkten (Residuen = 0). Die halbe beobachtete Veraenderung
            # dient als konservativer Streuungs-Proxy.
            sigma = max(sigma, abs(float(werte[1] - werte[0])) / 2.0)

        # Mindeststreuung relativ zu einem robusten Bedarfsniveau.
        basis = max(niveau, float(np.median(werte)), 0.0)
        sigma_min = self.min_variationskoeffizient * basis
        return float(max(sigma, sigma_min, 0.0))


def _theil_sen_steigung(x: np.ndarray, y: np.ndarray) -> float:
    """Median aller paarweisen Steigungen (ausreisserrobuste Regression)."""
    n = x.size
    if n < 2:
        return 0.0
    zeilen, spalten = np.triu_indices(n, k=1)
    dx = x[spalten] - x[zeilen]
    dy = y[spalten] - y[zeilen]
    gueltig = dx != 0
    if not np.any(gueltig):
        return 0.0
    return float(np.median(dy[gueltig] / dx[gueltig]))
