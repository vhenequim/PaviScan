"""Leitura de config/params.yaml. Limiares moram la, nunca no codigo."""

from __future__ import annotations

import os
from pathlib import Path

import yaml


def localizar(caminho: Path | None = None) -> Path:
    """Ordem: argumento, PAVISCAN_CONFIG, ./config/params.yaml, raiz do repositorio.

    Caminho pedido de forma explicita (argumento ou variavel) tem que existir: cair no
    padrao em silencio faria o usuario achar que rodou com outros limiares.
    """
    pedido = caminho or os.environ.get("PAVISCAN_CONFIG")
    if pedido:
        if not Path(pedido).is_file():
            raise FileNotFoundError(f"arquivo de configuracao nao encontrado: {pedido}")
        return Path(pedido)
    for c in (
        Path.cwd() / "config" / "params.yaml",
        Path(__file__).resolve().parents[2] / "config" / "params.yaml",
    ):
        if c.is_file():
            return c
    raise FileNotFoundError("config/params.yaml nao encontrado; informe --config")


def carregar(caminho: Path | None = None) -> dict:
    with open(localizar(caminho), encoding="utf-8") as f:
        return yaml.safe_load(f)
