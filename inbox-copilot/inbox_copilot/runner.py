"""Modul M-1 Phase 1b - duenne CLI-Huelle um ``CopilotService``.

    python -m inbox_copilot.runner --customer weber --auth
    python -m inbox_copilot.runner --customer weber --once
    python -m inbox_copilot.runner --customer weber --loop [--interval 10]
    python -m inbox_copilot.runner --customer weber --message-id <gmail_id>
    python -m inbox_copilot.runner --customer weber --stats

Keine Geschaeftslogik. Terminal-Ausgabe pro Nachricht genau eine Zeile:
``hash[:8] | STATUS | labels | total_ms`` - nie Betreff, Absender oder Body.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import yaml
from pydantic import SecretStr

from inbox_copilot.config import Settings
from inbox_copilot.gmail_adapter import GmailAdapter, OAuthDesktopAuth
from inbox_copilot.pipeline import AuditLogger, LLMClient
from inbox_copilot.schemas import CustomerConfig, PipelineOutcome
from inbox_copilot.service import CopilotService, StateStore

__all__ = ["format_outcome_line", "load_customer", "main"]

CUSTOMERS_DIR = Path("customers")


def load_customer(name: str, *, base_dir: Path = CUSTOMERS_DIR) -> CustomerConfig:
    """``customers/<name>.yaml``; Fallback ``customers/<name>.example.yaml``."""
    for kandidat in (base_dir / f"{name}.yaml", base_dir / f"{name}.example.yaml"):
        if kandidat.exists():
            daten = yaml.safe_load(kandidat.read_text(encoding="utf-8")) or {}
            return CustomerConfig.model_validate(daten)
    raise FileNotFoundError(f"Keine Kundendatei fuer '{name}' unter {base_dir}")


def format_outcome_line(outcome: PipelineOutcome) -> str:
    labels = ",".join(outcome.labels_set) or "-"
    return (
        f"{outcome.audit.message_id_hash[:8]} | {outcome.status} | {labels} | "
        f"{outcome.audit.total_ms}"
    )


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Minimaler .env-Loader (KEY=VALUE), setzt nur fehlende Variablen."""
    if not path.exists():
        return
    for zeile in path.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        schluessel, wert = zeile.split("=", 1)
        os.environ.setdefault(schluessel.strip(), wert.strip().strip("'\""))


def _settings_from_env(customer: CustomerConfig, *, interval: int | None) -> Settings:
    """Settings aus Umgebung (.env) plus Kunden- und CLI-Ueberschreibungen."""
    schluessel = os.environ.get("ANTHROPIC_API_KEY", "")
    if not schluessel:
        raise SystemExit("ANTHROPIC_API_KEY fehlt (siehe .env.example)")
    settings = Settings(
        anthropic_api_key=SecretStr(schluessel), mail_client=customer.mail_client
    )
    if interval is not None:
        settings = settings.model_copy(update={"poll_interval_s": interval})
    return settings


def _build_service(settings: Settings, customer: CustomerConfig) -> CopilotService:
    adapter = GmailAdapter(settings, auth=OAuthDesktopAuth(settings))
    state = StateStore(
        settings.gmail_state_path, ring_size=settings.processed_ring_size
    )
    return CopilotService(
        settings=settings,
        customer=customer,
        adapter=adapter,
        llm=LLMClient(settings),
        audit=AuditLogger(),
        state=state,
    )


async def _run(
    args: argparse.Namespace, settings: Settings, customer: CustomerConfig
) -> int:
    service = _build_service(settings, customer)
    await service.startup()

    if args.message_id:
        print(format_outcome_line(await service.process_message(args.message_id)))
        return 0

    if args.once:
        vorher = service.stats().messages_seen
        await service.run_once()
        stats = service.stats()
        print(
            f"run_once: {stats.messages_seen - vorher} Nachrichten | {stats.by_status}"
        )
        return 0

    if args.loop:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        print(f"loop: alle {settings.poll_interval_s}s | Ctrl+C beendet")
        await service.run_loop(stop)
        return 0

    if args.stats:
        print(service.stats().model_dump_json(indent=2))
        return 0

    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="inbox_copilot.runner")
    parser.add_argument("--customer", required=True, help="Name der Kundendatei")
    parser.add_argument(
        "--interval", type=int, default=None, help="Poll-Intervall in s"
    )
    modus = parser.add_mutually_exclusive_group(required=True)
    modus.add_argument("--auth", action="store_true", help="OAuth-Browser-Flow")
    modus.add_argument("--once", action="store_true", help="Ein Durchlauf")
    modus.add_argument("--loop", action="store_true", help="Endlosschleife")
    modus.add_argument("--message-id", default=None, help="Genau eine Gmail-ID")
    modus.add_argument("--stats", action="store_true", help="Kennzahlen als JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
    _load_dotenv()

    customer = load_customer(args.customer)
    settings = _settings_from_env(customer, interval=args.interval)

    if args.auth:
        OAuthDesktopAuth(settings).run_flow()
        print("auth: Token gespeichert")
        return 0

    return asyncio.run(_run(args, settings, customer))


if __name__ == "__main__":
    sys.exit(main())
