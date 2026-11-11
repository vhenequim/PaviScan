"""Acervo de imagens: uma pasta por ortofoto, com orto.tif e, se houver, pista e eixo.

E a entrada de arquivos do painel: a imagem enviada fica guardada aqui, conferida, e pode
ser reprocessada depois. Copiar uma pasta com `orto.tif` para o acervo tem o mesmo efeito
de enviar pela tela.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import rasterio

from . import config as config_mod
from . import exportar, ingestao


@dataclass
class Imagem:
    nome: str
    pasta: Path

    @property
    def orto(self) -> Path:
        return self.pasta / "orto.tif"

    @property
    def pista(self) -> Path | None:
        arq = self.pasta / "pista.geojson"
        return arq if arq.is_file() else None

    @property
    def eixo(self) -> Path | None:
        arq = self.pasta / "eixo.geojson"
        return arq if arq.is_file() else None


def pasta_imagens(config: Path | None = None) -> Path:
    """PAVISCAN_IMAGENS, ou `acervo.pasta_imagens` do params.yaml (relativa a raiz do projeto)."""
    if os.environ.get("PAVISCAN_IMAGENS"):
        return Path(os.environ["PAVISCAN_IMAGENS"])
    arq = config_mod.localizar(config)
    pasta = Path(config_mod.carregar(config)["acervo"]["pasta_imagens"])
    return pasta if pasta.is_absolute() else arq.parent.parent / pasta


def nome_seguro(texto: str) -> str:
    """So letras, numeros, `_` e `-`: o nome vira pasta."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", texto.strip()).strip("_")


def listar(pasta: Path) -> list[Imagem]:
    """Imagens do acervo: subpastas que tem orto.tif."""
    pasta = Path(pasta)
    if not pasta.is_dir():
        return []
    return [Imagem(p.name, p) for p in sorted(pasta.iterdir()) if (p / "orto.tif").exists()]


def metadados(orto: Path) -> dict:
    """Tamanho, GSD e SRC lidos do cabecalho, sem ler a imagem."""
    with rasterio.open(orto) as src:
        crs_origem = src.crs.to_string() if src.crs else ""
        bandas, alfa = src.count, ingestao.tem_alfa(src)
    with ingestao.abrir(orto) as ds:
        return {
            "largura_px": int(ds.width),
            "altura_px": int(ds.height),
            "gsd_cm": 100 * ingestao.gsd_m(ds),
            "src": crs_origem,
            "src_trabalho": ds.crs.to_string(),
            "reprojetada": ds.crs.to_string() != crs_origem,
            "bandas": bandas,
            "tem_alfa": alfa,
            "tamanho_mb": Path(orto).stat().st_size / 1e6,
        }


def _validar(arq: Path) -> None:
    try:
        with rasterio.open(arq) as src:
            if src.crs is None:
                raise ValueError("a imagem nao tem georreferencia (SRC)")
            if src.count < 1:
                raise ValueError("a imagem nao tem bandas")
    except rasterio.errors.RasterioIOError as e:
        raise ValueError(f"o arquivo nao e um GeoTIFF legivel: {e}") from e


def guardar(origem, nome: str, pasta: Path, substituir: bool = False) -> Imagem:
    """Grava a ortofoto no acervo como `<pasta>/<nome>/orto.tif`, conferindo a georreferencia.

    `origem` e um caminho ou um arquivo aberto (o upload do painel). Grava em arquivo parcial
    e so renomeia depois de validar: envio interrompido ou invalido nao deixa lixo no lugar.
    Substituir a imagem apaga a pista, o eixo e a previa antigos, que eram da outra imagem.
    """
    nome = nome_seguro(nome)
    if not nome:
        raise ValueError("de um nome para a imagem")
    destino = Path(pasta) / nome
    img = Imagem(nome, destino)
    if img.orto.exists() and not substituir:
        raise FileExistsError(f"ja existe uma imagem chamada {nome}")
    destino.mkdir(parents=True, exist_ok=True)
    parcial = destino / "orto.tif.parcial"
    try:
        if isinstance(origem, (str, Path)):
            shutil.copyfile(origem, parcial)
        else:
            with open(parcial, "wb") as f:
                shutil.copyfileobj(origem, f, length=8 * 1024 * 1024)
        _validar(parcial)
        # o que era da imagem antiga sai junto, inclusive o credito (stac_item.json)
        for velho in ("pista.geojson", "eixo.geojson", "vias_osm.json", "stac_item.json"):
            (destino / velho).unlink(missing_ok=True)
        shutil.rmtree(destino / "previa_rapida", ignore_errors=True)
        os.replace(parcial, img.orto)
    finally:
        parcial.unlink(missing_ok=True)
        if destino.is_dir() and not any(destino.iterdir()):
            destino.rmdir()  # envio que falhou nao deixa pasta vazia no acervo
    return img


def guardar_vetor(origem, imagem: Imagem, sufixo: str = ".geojson") -> str:
    """Guarda um vetor enviado como pista (poligonos) ou eixo (linhas). Devolve qual foi."""
    temp = imagem.pasta / f"vetor.parcial{sufixo}"
    try:
        if isinstance(origem, (str, Path)):
            shutil.copyfile(origem, temp)
        else:
            temp.write_bytes(origem.read())
        try:
            gdf = gpd.read_file(temp)
        except Exception as e:  # o leitor de vetor levanta tipos variados para arquivo ruim
            raise ValueError(f"nao consegui ler o arquivo de vetor: {e}") from e
    finally:
        temp.unlink(missing_ok=True)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    if gdf.empty or gdf.crs is None:
        raise ValueError("o vetor esta vazio ou sem SRC")
    tipos = set(gdf.geom_type)
    if tipos <= {"Polygon", "MultiPolygon"}:
        qual = "pista"
    elif tipos <= {"LineString", "MultiLineString"}:
        qual = "eixo"
    else:
        raise ValueError("o vetor precisa ter so poligonos (pista) ou so linhas (eixo da via)")
    gdf.to_file(imagem.pasta / f"{qual}.geojson", driver="GeoJSON")
    if qual == "eixo":  # eixo novo invalida a pista gerada a partir do antigo
        (imagem.pasta / "pista.geojson").unlink(missing_ok=True)
    return qual


def gravar_eixo(imagem: Imagem, linhas: list[dict]) -> Path:
    """Grava o eixo desenhado na tela. `linhas`: geometrias GeoJSON LineString em EPSG:4326."""
    feicoes = [
        {"type": "Feature", "properties": {"nome": f"Via {i + 1}"}, "geometry": g}
        for i, g in enumerate(linhas)
        if g.get("type") == "LineString" and len(g.get("coordinates", [])) >= 2
    ]
    if not feicoes:
        raise ValueError("desenhe pelo menos uma linha sobre a via")
    arq = imagem.pasta / "eixo.geojson"
    arq.write_text(json.dumps({"type": "FeatureCollection", "features": feicoes}))
    (imagem.pasta / "pista.geojson").unlink(missing_ok=True)
    return arq


def previa_rapida(imagem: Imagem, config: Path | None = None) -> tuple[Path, list[list[float]]]:
    """Previa pequena da ortofoto para a tela de entrada, gerada uma vez e guardada.

    Devolve (arquivo PNG, [[lat_s, lon_o], [lat_n, lon_l]]).
    """
    pasta = imagem.pasta / "previa_rapida"
    png, lim = pasta / "previa.png", pasta / "limites.json"
    if png.is_file() and lim.is_file() and png.stat().st_mtime >= imagem.orto.stat().st_mtime:
        return png, json.loads(lim.read_text())
    params = config_mod.carregar(config)
    pasta.mkdir(exist_ok=True)
    with ingestao.abrir(imagem.orto) as ds:
        escala = ingestao.escala_uint8(ds, params["raster"])
    limites = exportar.previa(
        imagem.orto, pasta, int(params["acervo"]["previa_lado_px"]), params["moldura"], escala
    )
    lim.write_text(json.dumps(limites))
    return png, limites
