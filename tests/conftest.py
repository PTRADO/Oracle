"""Socle des tests : rend `oracle` importable et sert les fixtures réelles."""
from __future__ import annotations

import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))

from oracle import _decoder  # noqa: E402


def fixture_texte(nom: str) -> str:
    """Texte décodé d'une fixture — passe par `_decoder` pour que les tests
    exercent aussi la détection d'encodage (les archives FDJ mêlent utf-8 et
    latin-1 selon les époques)."""
    return _decoder((FIXTURES / f"{nom}.csv").read_bytes())
