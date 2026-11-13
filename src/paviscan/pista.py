"""Poligono da pista: leitura, recorte, mascara por janela e segmentos ao longo do eixo."""

from __future__ import annotations

import math
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
from rasterio.features import rasterize
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union


def carregar(caminho: Path, crs, aviso_sobreposicao: float = 0.0) -> gpd.GeoDataFrame:
    """Le o GeoJSON da pista e leva para o SRC da ortofoto. Uma linha por trecho.

    Trechos que se sobrepoem sao aparados (fica o que veio primeiro), para a area e a
    extensao da pista nao contarem em dobro. Avisa quando a parte aparada passa da
    fracao `aviso_sobreposicao` da area da pista.
    """
    g = gpd.read_file(caminho)
    if g.crs is None:
        raise ValueError(f"{caminho} nao tem SRC")
    g = g.to_crs(crs)
    g = g[g.geometry.notna() & ~g.geometry.is_empty].copy()
    g = g[g.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    if g.empty:
        raise ValueError(f"{caminho} nao tem poligono de pista")
    col = next((c for c in ("nome", "name") if c in g.columns), None)
    nomes = g[col] if col else [None] * len(g)
    nomes = [
        str(n) if n is not None and str(n) not in ("", "nan", "None") else f"trecho {i + 1}"
        for i, n in enumerate(nomes)
    ]
    linhas, ocupado, aparada = [], None, 0.0
    for nome, geom in zip(nomes, g.geometry.buffer(0), strict=True):
        if ocupado is not None:
            limpa = geom.difference(ocupado)
            aparada += geom.area - limpa.area
            geom = limpa
        if geom.is_empty or geom.area <= 0:
            continue
        linhas.append({"nome": nome, "geometry": geom})
        ocupado = geom if ocupado is None else unary_union([ocupado, geom])
    if aparada > aviso_sobreposicao * sum(linha["geometry"].area for linha in linhas):
        warnings.warn(
            f"{caminho.name if isinstance(caminho, Path) else caminho}: trechos de pista se "
            f"sobrepoem em {aparada:.1f} m2; a sobreposicao foi contada uma vez so",
            stacklevel=2,
        )
    return gpd.GeoDataFrame(linhas, geometry="geometry", crs=crs)


def recortar(pista: gpd.GeoDataFrame, area: BaseGeometry) -> gpd.GeoDataFrame:
    """Mantem so a pista que cai onde a ortofoto tem dado. Erro se nao sobrar nada."""
    p = pista.copy()
    p["geometry"] = p.geometry.intersection(area)
    p = p[~p.geometry.is_empty & (p.geometry.area > 0)].reset_index(drop=True)
    if p.empty:
        raise ValueError("a pista nao cruza a area com dado da ortofoto; confira o arquivo e o SRC")
    return p


def mascara(pista: gpd.GeoDataFrame, transform, forma: tuple[int, int]) -> np.ndarray:
    """Mascara bool HxW (True = pista) para a janela com esse `transform`."""
    h, w = forma
    esq, topo = transform @ (0, 0)
    dir_, base = transform @ (w, h)
    quadro = box(min(esq, dir_), min(topo, base), max(esq, dir_), max(topo, base))
    geoms = [g for g in pista.geometry if g.intersects(quadro)]
    if not geoms:
        return np.zeros(forma, dtype=bool)
    return rasterize(geoms, out_shape=forma, transform=transform, fill=0, default_value=1).astype(
        bool
    )


def _eixo(pol) -> tuple[np.ndarray, np.ndarray]:
    """Direcao do eixo maior (retangulo minimo rotacionado) e sua normal, unitarias."""
    ret = pol.minimum_rotated_rectangle
    if ret.geom_type != "Polygon":
        return np.array([1.0, 0.0]), np.array([0.0, 1.0])
    p = np.asarray(ret.exterior.coords)[:4]
    a, b = p[1] - p[0], p[2] - p[1]
    d = a if np.hypot(*a) >= np.hypot(*b) else b
    d = d / np.hypot(*d)
    # sentido fixo: oeste -> leste; em via norte-sul, norte -> sul
    if (abs(d[0]) >= abs(d[1]) and d[0] < 0) or (abs(d[0]) < abs(d[1]) and d[1] > 0):
        d = -d
    return d, np.array([-d[1], d[0]])


def segmentar(
    pista: gpd.GeoDataFrame, comprimento_m: float, resto_min_m: float = 0.0
) -> gpd.GeoDataFrame:
    """Corta cada trecho em faixas de `comprimento_m` ao longo do eixo maior.

    Os segmentos cobrem a pista inteira, sem sobra e sem sobreposicao. `segmento` e
    sequencial a partir de 0, seguindo a ordem dos trechos. Sobra menor que `resto_min_m`
    no fim do trecho entra no ultimo segmento em vez de virar um segmento minusculo.
    """
    linhas = []
    for trecho, (nome, pol) in enumerate(zip(pista["nome"], pista.geometry, strict=True)):
        d, n = _eixo(pol)
        angulo = round(math.degrees(math.atan2(d[1], d[0])), 2)
        pts = np.asarray(pol.convex_hull.exterior.coords)
        s, t = pts @ d, pts @ n
        s0, s1 = s.min(), s.max()
        total = s1 - s0
        folga = total + (t.max() - t.min())  # as faixas sobram para fora do trecho, nunca faltam
        t0, t1 = t.min() - folga, t.max() + folga
        n_seg = max(1, math.ceil(total / comprimento_m - 1e-6))
        if n_seg > 1 and total - (n_seg - 1) * comprimento_m < resto_min_m:
            n_seg -= 1
        for k in range(n_seg):
            ultimo = k == n_seg - 1
            a = s0 + k * comprimento_m - (folga if k == 0 else 0.0)
            b = s1 + folga if ultimo else s0 + (k + 1) * comprimento_m
            faixa = Polygon([a * d + t0 * n, b * d + t0 * n, b * d + t1 * n, a * d + t1 * n])
            parte = pol.intersection(faixa)
            if parte.is_empty or parte.area <= 0:
                continue
            extensao = total - k * comprimento_m if ultimo else comprimento_m
            linhas.append(
                {
                    "segmento": len(linhas),
                    "via": nome,
                    "trecho": trecho,
                    "ordem": k,
                    "comprimento_m": round(extensao, 2),
                    "area_pista_m2": round(parte.area, 3),
                    "angulo_via": angulo,
                    "geometry": parte,
                }
            )
    return gpd.GeoDataFrame(linhas, geometry="geometry", crs=pista.crs)


def angulos(segmentos: gpd.GeoDataFrame) -> dict[int, float]:
    """Direcao da via em cada segmento: graus a partir do eixo x do SRC, anti-horario.

    Levantamento gravado antes da coluna `angulo_via`: recalcula pelo eixo maior do
    trecho inteiro (o mesmo que `segmentar` usou), ou do proprio segmento sem `trecho`.
    """
    if segmentos.empty:
        return {}
    if "angulo_via" in segmentos.columns:
        return dict(
            zip(
                segmentos["segmento"].astype(int),
                segmentos["angulo_via"].astype(float),
                strict=True,
            )
        )
    if "trecho" in segmentos.columns:
        chave = segmentos["trecho"]
    else:  # sem trecho, a via inteira (segmento curto de sobra daria a direcao errada)
        chave = segmentos["via"] if "via" in segmentos.columns else segmentos["segmento"]
    saida: dict[int, float] = {}
    for _, grupo in segmentos.groupby(chave):
        d, _n = _eixo(unary_union(list(grupo.geometry)))
        ang = round(math.degrees(math.atan2(d[1], d[0])), 2)
        saida.update({int(s): ang for s in grupo["segmento"]})
    return saida
