"""Contrato do detector. O YOLO do mestrado entra por aqui depois."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass
class Deteccao:
    mascara: np.ndarray  # bool HxW, um buraco por deteccao
    confianca: float  # 0 a 1
    classe: str = "panela"
    # regiao extensa: mede area, nao objeto. Pode cruzar janelas e segmentos; o pipeline
    # une os pedacos vistos em janelas vizinhas e reparte a area pelos segmentos
    extensa: bool = False


class Detector(Protocol):
    nome: str
    versao: str

    def detectar(self, img: np.ndarray, gsd_m: float, valida: np.ndarray) -> list[Deteccao]:
        """img: RGB uint8 HxWx3. valida: bool HxW (True = pista). Sem estado entre chamadas."""
        ...
