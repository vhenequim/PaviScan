"""Mascara de deteccao -> poligono em UTM com area, largura, comprimento e severidade."""

from __future__ import annotations

import math

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from affine import Affine
from rasterio.features import shapes
from shapely import STRtree, affinity
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .deteccao import Deteccao

# geometria, confianca, classe, folga (px ate a borda interna da janela), na_emenda, extensa
Item = tuple[BaseGeometry, float, str, float, bool, bool]

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
            itens.append(
                (unary_union(pols), float(d.confianca), d.classe, folga, emenda, d.extensa)
            )
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
    extensos = [it for it in itens if it[5]]
    itens = [it for it in itens if not it[5]]
    if len(itens) < 2:
        return list(itens) + unir_extensos(extensos)
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
    return [it for it, e in zip(itens, estado, strict=True) if e == 1] + unir_extensos(extensos)


def unir_extensos(itens: list[Item]) -> list[Item]:
    """Regioes extensas da mesma classe vistas em janelas vizinhas viram uma so, por uniao.

    As mascaras estao na mesma grade de pixels, entao a uniao e exata e nao conta area
    em dobro. Nao ha filtro de forma a preservar: regiao extensa mede area.
    """
    saida: list[Item] = []
    for classe in sorted({it[2] for it in itens}):
        da_classe = [it for it in itens if it[2] == classe]
        uniao = unary_union([it[0] for it in da_classe])
        for parte in uniao.geoms if hasattr(uniao, "geoms") else [uniao]:
            if parte.is_empty or parte.geom_type != "Polygon":
                continue
            conf = max(it[1] for it in da_classe if it[0].intersects(parte))
            saida.append((parte, conf, classe, float("inf"), False, True))
    return saida


def repartir(itens: list[Item], segmentos: gpd.GeoDataFrame, pedaco_min_m2: float) -> list[Item]:
    """Corta cada regiao extensa nas divisas dos segmentos, para a area ir ao segmento certo."""
    saida: list[Item] = []
    arvore = STRtree(list(segmentos.geometry))
    for it in itens:
        if not it[5]:
            saida.append(it)
            continue
        for k in arvore.query(it[0], predicate="intersects"):
            pedaco = it[0].intersection(segmentos.geometry.iloc[int(k)])
            for parte in pedaco.geoms if hasattr(pedaco, "geoms") else [pedaco]:
                if parte.geom_type == "Polygon" and parte.area >= pedaco_min_m2:
                    saida.append((parte, *it[1:]))
    return saida


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


def contorno_leve(geom: BaseGeometry, gsd_m: float, cfg: dict | None = None) -> BaseGeometry:
    """Contorno com menos vertices e a mesma area.

    A borda que sai da mascara segue os pixels, em escada, e um defeito grande passa de
    mil vertices. Sem `cfg`: so tira o serrilhado (tolerancia de meio pixel). Com `cfg`
    (bloco `contorno` do params.yaml): cada parte e simplificada com tolerancia que cresce
    com o tamanho dela e escalada em volta do proprio centroide para manter a area medida
    nos pixels. Parte cuja area mudaria mais que `variacao_area_max` (forma fina) fica so
    sem o serrilhado.
    """
    base = geom.simplify(gsd_m / 2).buffer(0)
    if not cfg or geom.area <= 0:
        return base
    lim = 1.0 + float(cfg["variacao_area_max"])

    def leve(p: BaseGeometry) -> BaseGeometry:
        area = float(p.area)
        tol = max(
            gsd_m / 2,
            float(cfg["tolerancia_min_m"]),
            float(cfg["tolerancia_rel"]) * math.sqrt(area),
        )
        g = p.simplify(tol).buffer(0)
        if g.is_empty or not 1.0 / lim < g.area / area < lim:
            return p.simplify(gsd_m / 2).buffer(0)
        f = math.sqrt(area / g.area)
        return affinity.scale(g, f, f, origin=g.centroid)

    partes = [g for g in (leve(p) for p in poligonos(geom)) if not g.is_empty]
    if not partes:
        return base
    return partes[0] if len(partes) == 1 else unary_union(partes)


def poligonos(geom: BaseGeometry | None) -> list:
    """Os Polygon de qualquer geometria (Multi, colecao com sobra de linha ou ponto, vazia)."""
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    return [p for parte in getattr(geom, "geoms", []) for p in poligonos(parte)]


def caixas(geoms, angulos_graus) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Menor retangulo com lados paralelo e perpendicular a via que envolve cada geometria.

    `angulos_graus`: direcao da via em cada geometria, em graus a partir do eixo x do SRC
    (anti-horario). Devolve (retangulos, lado ao longo da via, lado transversal), em metros.
    """
    g = np.asarray(geoms, dtype=object)
    n = len(g)
    if n == 0:
        return np.array([], dtype=object), np.zeros(0), np.zeros(0)
    xy, idx = shapely.get_coordinates(g, return_index=True)
    a = np.radians(np.asarray(angulos_graus, dtype=float))
    c, s = np.cos(a), np.sin(a)
    u = xy[:, 0] * c[idx] + xy[:, 1] * s[idx]  # ao longo da via
    v = -xy[:, 0] * s[idx] + xy[:, 1] * c[idx]  # atravessando a via
    u0, u1, v0, v1 = (
        np.full(n, np.inf),
        np.full(n, -np.inf),
        np.full(n, np.inf),
        np.full(n, -np.inf),
    )
    np.minimum.at(u0, idx, u)
    np.maximum.at(u1, idx, u)
    np.minimum.at(v0, idx, v)
    np.maximum.at(v1, idx, v)
    vazio = ~np.isfinite(u0)
    u0[vazio] = u1[vazio] = v0[vazio] = v1[vazio] = 0.0
    cu = np.stack([u0, u1, u1, u0, u0], axis=1)
    cv = np.stack([v0, v0, v1, v1, v0], axis=1)
    x = cu * c[:, None] - cv * s[:, None]
    y = cu * s[:, None] + cv * c[:, None]
    rets = shapely.polygons(np.stack([x, y], axis=-1))
    rets[vazio] = None
    return rets, u1 - u0, v1 - v0


def com_caixa(defeitos: gpd.GeoDataFrame, angulos: dict[int, float]) -> gpd.GeoDataFrame:
    """Acrescenta a caixa de cada defeito: retangulo paralelo a via que o envolve.

    Colunas `angulo_via` (graus), `caixa_ao_longo_m` e `caixa_transversal_m`. A direcao
    da via vem do segmento do defeito (`angulos`: segmento -> graus).
    """
    d = defeitos.copy()
    if d.empty or "segmento" not in d.columns:
        for col in ("angulo_via", "caixa_ao_longo_m", "caixa_transversal_m"):
            d[col] = np.zeros(len(d))
        return d
    # segmento sem direcao conhecida (nao acontece no pipeline): retangulo alinhado ao SRC
    ang = d["segmento"].map(lambda s: angulos.get(int(s), 0.0) if pd.notna(s) else 0.0)
    ang = ang.astype(float).to_numpy()
    _, ao_longo, transversal = caixas(d.geometry.values, ang)
    d["angulo_via"] = np.round(ang, 2)
    d["caixa_ao_longo_m"] = np.round(ao_longo, 3)
    d["caixa_transversal_m"] = np.round(transversal, 3)
    return d


def caixas_de(defeitos: gpd.GeoDataFrame) -> gpd.GeoSeries:
    """Os retangulos paralelos a via (precisa de `angulo_via`; sem ela, alinhados ao SRC)."""
    ang = defeitos["angulo_via"] if "angulo_via" in defeitos.columns else np.zeros(len(defeitos))
    rets, _, _ = caixas(defeitos.geometry.values, np.asarray(ang, dtype=float))
    return gpd.GeoSeries(rets, index=defeitos.index, crs=defeitos.crs)


def medir(
    itens: list[Item], crs, gsd_m: float, faixas: dict, contorno: dict | None = None
) -> gpd.GeoDataFrame:
    """Tabela de defeitos. Toda medida sai da geometria em SRC projetado.

    `contorno` (bloco do params.yaml): contorno com menos vertices, mesma area.
    """
    linhas = []
    for geom, conf, classe, _folga, emenda, _extensa in itens:
        g = contorno_leve(geom, gsd_m, contorno)
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
