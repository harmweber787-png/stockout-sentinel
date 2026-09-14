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
from kmu_discovery.models import CompanyProfile, DocumentKind, TextDocument

_ADAPTER = TypeAdapter(list[CompanyProfile])


def _demo_companies() -> list[CompanyProfile]:
    now = datetime(2026, 9, 14, 8, 0, tzinfo=UTC)
    return [
        CompanyProfile(
            uid="CHE-100.000.001",
            name="Zahnarztpraxis Seefeld AG",
            noga_codes=["86.23"],
            zweck="Betrieb einer Zahnarztpraxis.",
        ),
        CompanyProfile(
            uid="CHE-100.000.002",
            name="Muster Logistik AG",
            noga_codes=["52.29"],
            zweck="Nationale und internationale Transporte.",
            website="https://muster-logistik.ch",
            emails=["info@bluewin.ch"],
            documents=[
                TextDocument(
                    kind=DocumentKind.JOB_POSTING,
                    url="https://muster-logistik.ch/jobs/sachbearbeiterin",
                    text=(
                        "Sachbearbeiterin Administration 60%. Erfassung von Auftraegen, "
                        "Ablage, Korrespondenz. Gute MS-Office-Kenntnisse, insbesondere "
                        "Excel, werden vorausgesetzt."
                    ),
                    retrieved_at=now,
                ),
            ],
        ),
        CompanyProfile(
            uid="CHE-100.000.003",
            name="Beispiel Handels GmbH",
            noga_codes=["46.90"],
            website="https://beispiel-handel.ch",
            documents=[
                TextDocument(
                    kind=DocumentKind.JOB_POSTING,
                    url="https://beispiel-handel.ch/karriere",
                    text=(
                        "Fuer unsere Buchhaltung setzen wir Abacus ein; "
                        "Abacus-Kenntnisse von Vorteil."
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
    args = parser.parse_args(argv)

    companies = _demo_companies() if args.demo else _load(args.input)
    gates = default_gates()
    reports = [run_gates(company, gates) for company in companies]

    if args.format == "json":
        payload = [report.model_dump(mode="json") for report in reports]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    for company, report in zip(companies, reports, strict=True):
        print(f"{report.uid}  {company.name}")
        print(f"  Urteil: {report.outcome.upper()}")
        if report.flags:
            print(f"  Flags:  {', '.join(report.flags)}")
        for result in report.results:
            for reason in result.reasons()[:5]:
                print(f"    - {result.gate}: {reason}")
            for note in result.notes:
                print(f"    ! {result.gate}: {note}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
