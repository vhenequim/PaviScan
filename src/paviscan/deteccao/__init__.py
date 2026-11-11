"""Detectores de buraco atras de uma interface unica."""

from __future__ import annotations

from .base import Deteccao, Detector

__all__ = ["Deteccao", "Detector", "criar"]


def criar(nome: str, params: dict | None = None) -> Detector:
    """Fabrica: devolve o detector pedido em `detector:` do params.yaml."""
    if nome == "classico":
        from .classico import DetectorClassico

        return DetectorClassico(**(params or {}))
    if nome == "yolo":
        raise NotImplementedError("detector yolo ainda nao plugado (depende dos pesos do mestrado)")
    raise ValueError(f"detector desconhecido: {nome}")
