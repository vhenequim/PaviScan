"""Mascara de deteccao -> poligono em UTM com area, largura, comprimento e severidade."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
from affine import Affine
from rasterio.features import shapes
from shapely import STRtree
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .deteccao import Deteccao

# geometria, confianca, classe, folga (px ate a borda interna da janela), na_emenda
Item = tuple[BaseGeometry, float, str, float, bool]

COLUNAS = [
    "area_m2", "largura_m", "comprimento_m", "confianca", "severidade", "classe", "na_emenda",
]  # fmt: skip


def vetorizar(
    deteccoes: list[Deteccao],
    transform: Affine,
    folgas: list[float] | None = None,
    emendas: list[bool] | None = None,
) -> list[Item]:
    """Cada mascara vira um poligono no SRC do raster, usando o transform da janela."""
    itens: list[Item] = []
    for k, d in enumerate(deteccoes):
        ys, xs = np.nonzero(d.mascara)
        if ys.size == 0:
            continue
        y0, x0 = int(ys.min()), int(xs.min())
        rec = d.mascara[y0 : ys.max() + 1, x0 : xs.max() + 1].astype(np.uint8)
        tf = transform @ Affine.translation(x0, y0)
        pols = [shape(g) for g, v in shapes(rec, mask=rec > 0, connectivity=8, transform=tf) if v]
        if pols:
            folga = float(folgas[k]) if folgas is not None else float("inf")
            emenda = bool(emendas[k]) if emendas is not None else False
            itens.append((unary_union(pols), float(d.confianca), d.classe, folga, emenda))
    return itens


def deduplicar(itens: list[Item], fracao_min: float) -> list[Item]:
    """Do mesmo buraco visto em janelas vizinhas, fica so a versao maior.

    Duas deteccoes sao o mesmo buraco quando a intersecao cobre pelo menos `fracao_min`
    da menor delas. Vizinhas que so se encostam (partes separadas pelo watershed) nao
    sao duplicata: em coordenadas reais elas tem intersecao de area quase nula, nunca
    exatamente zero. A versao cortada pela borda de uma janela e sempre a menor, por
    isso vence a maior; a folga ate a borda so desempata.

    Nao ha uniao de poligonos: toda geometria que sai daqui e exatamente uma deteccao,
    entao os filtros de forma do detector continuam valendo na saida.
    """
    if len(itens) < 2:
        return list(itens)
    geoms = [it[0] for it in itens]
    areas = [g.area for g in geoms]
    arvore = STRtree(geoms)
    ordem = sorted(range(len(itens)), key=lambda i: (-areas[i], -itens[i][3], i))
    estado = [0] * len(itens)  # 0 = a decidir, 1 = fica, -1 = sai
    for i in ordem:
        if estado[i]:
            continue
        estado[i] = 1
        for j in arvore.query(geoms[i], predicate="intersects"):
            j = int(j)
            if estado[j] or areas[j] <= 0:
                continue
            if geoms[i].intersection(geoms[j]).area >= fracao_min * min(areas[i], areas[j]):
                estado[j] = -1
    return [it for it, e in zip(itens, estado, strict=True) if e == 1]


def lados_ret_min(geom: BaseGeometry) -> tuple[float, float]:
    """Lados do retangulo minimo rotacionado: (largura, comprimento), largura <= comprimento."""
    ret = geom.minimum_rotated_rectangle
    if ret.geom_type != "Polygon":
        return 0.0, float(ret.length)
    p = np.asarray(ret.exterior.coords)
    a, b = float(np.hypot(*(p[1] - p[0]))), float(np.hypot(*(p[2] - p[1])))
    return min(a, b), max(a, b)


def severidade(area_m2: float, faixas: dict) -> str:
    """Tres faixas por area. Limites em params.yaml; nao e classificacao de norma."""
    if area_m2 >= faixas["alta_a_partir_de"]:
        return "alta"
    if area_m2 >= faixas["media_a_partir_de"]:
        return "media"
    return "baixa"


def medir(itens: list[Item], crs, gsd_m: float, faixas: dict) -> gpd.GeoDataFrame:
    """Tabela de defeitos. Toda medida sai da geometria em SRC projetado."""
    linhas = []
    for geom, conf, classe, _folga, emenda in itens:
        g = geom.simplify(gsd_m / 2).buffer(0)
        if g.is_empty or g.area <= 0:
            continue
        largura, comprimento = lados_ret_min(g)
        area = round(float(g.area), 4)
        linhas.append(
            {
                "area_m2": area,
                "largura_m": round(largura, 3),
                "comprimento_m": round(comprimento, 3),
                "confianca": round(float(np.clip(conf, 0.0, 1.0)), 3),
                "severidade": severidade(area, faixas),
                "classe": classe,
                "na_emenda": bool(emenda),
                "geometry": g,
            }
        )
    if not linhas:
        return gpd.GeoDataFrame({c: [] for c in COLUNAS}, geometry=[], crs=crs)
    return gpd.GeoDataFrame(linhas, geometry="geometry", crs=crs)
