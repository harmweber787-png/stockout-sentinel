"""Gemeinsame Fixtures fuer die Gate-Tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from kmu_discovery.models import CompanyProfile, DocumentKind, TextDocument

FIXTURE_DIR = Path(__file__).parent / "fixtures"
RETRIEVED_AT = datetime(2026, 9, 14, 8, 0, tzinfo=UTC)


@pytest.fixture(scope="session")
def page() -> PageLoader:
    return PageLoader(FIXTURE_DIR)


class PageLoader:
    """Laedt anonymisierte Beispielseiten aus ``fixtures/``."""

    def __init__(self, directory: Path) -> None:
        """Merkt sich das Fixture-Verzeichnis."""
        self._directory = directory

    def __call__(self, name: str) -> str:
        """Liefert den Text der Beispielseite ``name``."""
        return (self._directory / f"{name}.txt").read_text(encoding="utf-8")


def make_document(
    text: str,
    kind: DocumentKind = DocumentKind.WEBSITE_PAGE,
    url: str | None = "https://beispiel-betrieb.ch/seite",
) -> TextDocument:
    """Baut ein Textdokument mit festem Zeitstempel."""
    return TextDocument(kind=kind, url=url, text=text, retrieved_at=RETRIEVED_AT)


def make_company(
    name: str = "Beispiel Betrieb GmbH",
    uid: str = "CHE-100.000.001",
    **kwargs: object,
) -> CompanyProfile:
    """Baut ein minimales Betriebsprofil; ``kwargs`` ueberschreiben Felder."""
    data: dict[str, object] = {"uid": uid, "name": name}
    data.update(kwargs)
    return CompanyProfile.model_validate(data)
