"""Swiss KMU Problem-Discovery-Engine.

Findet Schweizer Betriebe mit nachweisbarem Administrations-Schmerz und
trennt bezahlbaren, erreichbaren, haftungsarmen Schmerz von blossem Schmerz.

Rechtlicher Rahmen (nicht verhandelbar, siehe README):
  * revDSG: Personendaten nur aus offiziellen Registern, zweckgebunden.
  * UWG Art. 3 Abs. 1 lit. o: kein automatisierter Werbeversand. Diese
    Bibliothek enthaelt keine Versandlogik und wird keine bekommen.
  * UWG Art. 5 lit. c: kein AGB-widriges Scraping, keine Captcha-Umgehung.

Modul 1 (dieses Paket): Datenmodelle und die deterministischen Gates.
"""

from kmu_discovery.gates.base import GateReport, run_gates
from kmu_discovery.gates.erp import ErpGate, ErpGateResult, ErpStatus
from kmu_discovery.gates.liability import LiabilityGate
from kmu_discovery.models import CompanyProfile, GateOutcome, GateResult
from kmu_discovery.output import RunStats

__all__ = [
    "CompanyProfile",
    "ErpGate",
    "ErpGateResult",
    "ErpStatus",
    "GateOutcome",
    "GateReport",
    "GateResult",
    "LiabilityGate",
    "RunStats",
    "default_gates",
    "run_gates",
]

__version__ = "0.1.0"


def default_gates() -> tuple[LiabilityGate, ErpGate]:
    """Die Standardkette: Haftungsfilter zuerst, dann ERP-Negativfilter."""
    return (LiabilityGate(), ErpGate())
