"""Baixa uma ortofoto do OpenAerialMap (imagery.hotosm.org) a partir do link ou do id."""

from __future__ import annotations

import json
import re
import shutil
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.imagery.hotosm.org/stac"


def _id(ref: str) -> str:
    """Aceita o link do navegador (com selected_id=...) ou o id direto."""
    consulta = urllib.parse.parse_qs(urllib.parse.urlparse(ref).query)
    ident = consulta.get("selected_id", [ref])[0].strip()
    if not re.fullmatch(r"[0-9a-fA-F]{24}", ident):
        raise ValueError(f"nao achei um id de imagem em: {ref}")
    return ident


def baixar(ref: str, pasta: Path) -> Path:
    """Grava orto.tif e stac_item.json (credito e licenca) em `pasta`. Devolve o orto.tif."""
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(f"{API}/search?ids={_id(ref)}", headers={"User-Agent": "paviscan"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        busca = json.loads(resp.read())
    if not busca.get("features"):
        raise RuntimeError("imagem nao encontrada no OpenAerialMap")
    item = busca["features"][0]
    href = item["assets"]["visual"]["href"]
    if urllib.parse.urlparse(href).scheme != "https":
        raise RuntimeError(f"endereco da imagem nao e https: {href}")
    (pasta / "stac_item.json").write_text(json.dumps(item))
    destino, parcial = pasta / "orto.tif", pasta / "orto.tif.parcial"
    req = urllib.request.Request(href, headers={"User-Agent": "paviscan"})
    # grava em arquivo parcial e so renomeia no fim: download interrompido nao deixa
    # um orto.tif truncado no lugar
    with urllib.request.urlopen(req, timeout=120) as resp, open(parcial, "wb") as f:
        shutil.copyfileobj(resp, f, length=1 << 20)
    parcial.replace(destino)
    return destino
