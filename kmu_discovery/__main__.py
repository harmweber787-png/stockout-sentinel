"""Beispielaufruf der Gate-Kette.

# Betriebe aus einer JSON-Datei pruefen (Liste von CompanyProfile-Objekten)
python -m kmu_discovery --input betriebe.json

# Maschinenlesbar, z. B. fuer die Pipeline
python -m kmu_discovery --input betriebe.json --format json

# Ohne Datei: eingebautes Beispiel
python -m kmu_discovery --demo
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

from kmu_discovery import default_gates, run_gates
from kmu_discovery.models import (
    CompanyProfile,
    DocumentKind,
    ErpGateResult,
    Groessenklasse,
    Rechtsform,
    Standort,
    TextDocument,
)
from kmu_discovery.output import DEFAULT_TOP_N, RunStats

_ADAPTER = TypeAdapter(list[CompanyProfile])


EXAMPLE_DIR = Path(__file__).resolve().parent / "examples"


def _example_page(name: str) -> str:
    """Liest eine anonymisierte Beispielseite aus ``examples/``."""
    return (EXAMPLE_DIR / f"{name}.txt").read_text(encoding="utf-8")


def _demo_companies() -> list[CompanyProfile]:
    """Vier Betriebe auf den vier Beispielseiten - je ein Urteilspfad."""
    now = datetime(2026, 9, 14, 8, 0, tzinfo=UTC)
    return [
        # PASS: Zielbetrieb - Papierprozess, Office-Inserat, Freemail, kein ERP.
        CompanyProfile(
            uid="CHE-100.000.001",
            name="Fahrschule Musterthal GmbH",
            rechtsform=Rechtsform.GMBH,
            noga_codes=["85.53"],
            groessenklasse=Groessenklasse.MIKRO,
            standort=Standort(kanton="BE", plz="3011", ort="Musterthal"),
            zweck="Betrieb einer Fahrschule sowie Durchführung von Verkehrskundeunterricht.",
            website="https://fahrschule-musterthal.ch",
            emails=["info@bluewin.ch"],
            documents=[
                TextDocument(
                    kind=DocumentKind.WEBSITE_PAGE,
                    url="https://fahrschule-musterthal.ch/anmeldung",
                    text=_example_page("website_fahrschule"),
                    retrieved_at=now,
                ),
                TextDocument(
                    kind=DocumentKind.JOB_POSTING,
                    url="https://fahrschule-musterthal.ch/jobs/sachbearbeiterin",
                    text=_example_page("job_ad_sachbearbeiterin"),
                    retrieved_at=now,
                ),
            ],
        ),
        # REJECT: Haftungsfeld Gesundheit - NOGA und Website decken sich.
        CompanyProfile(
            uid="CHE-100.000.002",
            name="Praxis am Dorfplatz AG",
            rechtsform=Rechtsform.AG,
            noga_codes=["86.21"],
            standort=Standort(kanton="ZH", plz="8001", ort="Musterdorf"),
            zweck="Führung einer Arztpraxis für Allgemeine Innere Medizin.",
            website="https://praxis-am-dorfplatz.ch",
            documents=[
                TextDocument(
                    kind=DocumentKind.WEBSITE_PAGE,
                    url="https://praxis-am-dorfplatz.ch/",
                    text=_example_page("website_arztpraxis"),
                    retrieved_at=now,
                ),
            ],
        ),
        # REVIEW: Zulieferer - Kontext-Veto verhindert den Ausschluss, aber der
        # Fall geht in die Pruefschlange statt stillschweigend durch.
        CompanyProfile(
            uid="CHE-100.000.003",
            name="Musterwerk Informatik GmbH",
            rechtsform=Rechtsform.GMBH,
            noga_codes=["62.01"],
            standort=Standort(kanton="SG", plz="9000", ort="Musterstadt"),
            zweck="Entwicklung und Vertrieb von Branchensoftware.",
            website="https://musterwerk-informatik.ch",
            documents=[
                TextDocument(
                    kind=DocumentKind.WEBSITE_PAGE,
                    url="https://musterwerk-informatik.ch/loesungen",
                    text=_example_page("website_software_anbieter"),
                    retrieved_at=now,
                ),
            ],
        ),
        # REJECT: ERP im Einsatz - Stelleninserat ist ein harter Nachweis.
        CompanyProfile(
            uid="CHE-100.000.004",
            name="Beispiel Handels GmbH",
            rechtsform=Rechtsform.GMBH,
            noga_codes=["46.90"],
            standort=Standort(kanton="AG", plz="5000", ort="Musterau"),
            website="https://beispiel-handel.ch",
            documents=[
                TextDocument(
                    kind=DocumentKind.JOB_POSTING,
                    url="https://beispiel-handel.ch/karriere",
                    text=(
                        "Sachbearbeiter/in Finanzen 80%. Für unsere Buchhaltung "
                        "setzen wir Abacus ein; Abacus-Kenntnisse sind Voraussetzung."
                    ),
                    retrieved_at=now,
                ),
            ],
        ),
    ]


def _load(path: Path) -> list[CompanyProfile]:
    raw = sys.stdin.read() if str(path) == "-" else path.read_text(encoding="utf-8")
    return _ADAPTER.validate_python(json.loads(raw))


def main(argv: Sequence[str] | None = None) -> int:
    """Beispielaufruf: Gate-Kette ueber eine Liste von Betrieben."""
    parser = argparse.ArgumentParser(description="Gate-Kette der Problem-Discovery-Engine.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="JSON-Datei mit Betrieben ('-' fuer stdin).")
    source.add_argument("--demo", action="store_true", help="Eingebautes Beispiel verwenden.")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP_N,
        help=f"Anzahl der haeufigsten REVIEW-Treffer in der Statistik (Standard {DEFAULT_TOP_N}).",
    )
    args = parser.parse_args(argv)

    companies = _demo_companies() if args.demo else _load(args.input)
    gates = default_gates()
    reports = [run_gates(company, gates) for company in companies]
    stats = RunStats.from_reports(reports, top_n=args.top)

    if args.format == "json":
        payload = {
            "stats": stats.model_dump(mode="json"),
            "reports": [report.model_dump(mode="json") for report in reports],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    for company, report in zip(companies, reports, strict=True):
        print(f"{report.uid}  {company.name}")
        print(f"  Urteil: {report.outcome.upper()}")
        if report.flags:
            print(f"  Flags:  {', '.join(report.flags)}")
        for result in report.results:
            print(f"  [{result.gate}] {result.outcome}", end="")
            if isinstance(result, ErpGateResult):
                print(
                    f" | Status {result.status} | Abwesenheitssignal {result.absence_signal}"
                    f" | Schmerzsignale {', '.join(result.pain_signals) or '-'}",
                    end="",
                )
            print()
            for reason in result.reasons()[:4]:
                print(f"      - {reason}")
            for note in result.notes:
                print(f"      ! {note}")
        print()

    print(stats.as_table())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
