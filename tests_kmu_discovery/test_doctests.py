"""Fuehrt die Doctests aller Module aus - Beispiele in Docstrings muessen stimmen."""

from __future__ import annotations

import doctest
import importlib
import pkgutil
from types import ModuleType

import pytest

import kmu_discovery


def _modules() -> list[str]:
    return [
        name
        for _, name, _ in pkgutil.walk_packages(kmu_discovery.__path__, "kmu_discovery.")
        if not name.endswith(".__main__")
    ] + ["kmu_discovery"]


@pytest.mark.parametrize("module_name", _modules())
def test_doctests(module_name: str) -> None:
    module: ModuleType = importlib.import_module(module_name)
    result = doctest.testmod(module, verbose=False)
    assert result.failed == 0, f"{module_name}: {result.failed} Doctest(s) fehlgeschlagen"
