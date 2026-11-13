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


ORDEM_SEVERIDADE = {"baixa": 0, "media": 1, "alta": 2}


def agrupar(
    defeitos: gpd.GeoDataFrame, cfg: dict, coluna: str = "grupo", fora=()
) -> gpd.GeoDataFrame:
    """Junta num retangulo so os defeitos que viram um remendo so: coluna `grupo`.

    `fora`: classes que nao entram neste agrupamento (ficam sem numero em `coluna`).

    Regras (bloco `agrupamento` do params.yaml):
    - retangulos que se tocam ou ficam a ate `distancia_juntar_m` se juntam;
    - defeito pequeno (menor lado do retangulo abaixo de `lado_min_m`, a dimensao minima
      de panela do manual LTPP) se junta ao vizinho a ate `distancia_pequeno_m` (a FHWA
      combina reparos a menos de 300 mm);
    - o retangulo do grupo e refeito e, se encostar em outro, os dois se juntam, ate parar.
    O retangulo do grupo segue a direcao da via do maior defeito dele (num cruzamento, a
    da via em que esta o maior).
    `grupo` vai de 1 em diante, na ordem do menor id de cada grupo.
    """
    d = defeitos.copy()
    if len(fora) and "classe" in d.columns and d["classe"].isin(list(fora)).any():
        dentro = ~d["classe"].isin(list(fora))
        d[coluna] = agrupar(d[dentro], cfg)["grupo"].reindex(d.index)
        return d
    n = len(d)
    if n == 0:
        d[coluna] = pd.Series(dtype="int64")
        return d
    ang = d["angulo_via"] if "angulo_via" in d.columns else pd.Series(0.0, index=d.index)
    ang = ang.fillna(0.0).astype(float).to_numpy()
    rets, ao_longo, transversal = caixas(d.geometry.values, ang)
    pequeno = np.minimum(ao_longo, transversal) < float(cfg["lado_min_m"])
    juntar, junto_pequeno = float(cfg["distancia_juntar_m"]), float(cfg["distancia_pequeno_m"])
    pai = list(range(n))

    def raiz(i: int) -> int:
        while pai[i] != i:
            pai[i] = pai[pai[i]]
            i = pai[i]
        return i

    def unir(i: int, j: int) -> bool:
        a, b = raiz(i), raiz(j)
        if a == b:
            return False
        pai[max(a, b)] = min(a, b)
        return True

    validos = np.array([r is not None and not r.is_empty for r in rets])
    arvore = STRtree(rets[validos])
    idx_validos = np.flatnonzero(validos)
    alcance = max(juntar, junto_pequeno)
    for i in idx_validos:
        for k in arvore.query(rets[i], predicate="dwithin", distance=alcance + 1e-9):
            j = int(idx_validos[k])
            if j <= i:
                continue
            limite = junto_pequeno if (pequeno[i] or pequeno[j]) else juntar
            if rets[i].distance(rets[j]) <= limite + 1e-9:
                unir(i, int(j))
    area = d.geometry.area.to_numpy()
    while True:  # o retangulo do grupo cresce e pode encostar em outro
        rotulo = np.array([raiz(i) for i in range(n)])
        chefes = sorted(set(rotulo[validos]))
        geoms, angs = [], []
        for c in chefes:
            membros = np.flatnonzero(rotulo == c)
            geoms.append(unary_union(list(d.geometry.values[membros])))
            angs.append(ang[membros[np.argmax(area[membros])]])
        rg, _, _ = caixas(np.array(geoms, dtype=object), np.array(angs))
        arv = STRtree(rg)
        mudou = False
        for a_, r in enumerate(rg):
            for b_ in arv.query(r, predicate="intersects"):
                if b_ > a_:
                    mudou |= unir(chefes[a_], chefes[int(b_)])
        if not mudou:
            break
    rotulo = np.array([raiz(i) for i in range(n)])
    ids = np.arange(1, n + 1)
    if "id" in d.columns:  # id faltando (nao acontece no pipeline) fica depois dos outros
        ids = pd.to_numeric(d["id"], errors="coerce").fillna(10**9).astype(int).to_numpy()
    menor_id = pd.Series(ids).groupby(rotulo).transform("min").to_numpy()
    ordem = {m: k + 1 for k, m in enumerate(sorted(set(menor_id)))}
    d[coluna] = [ordem[m] for m in menor_id]
    return d


def agrupar_tudo(defeitos: gpd.GeoDataFrame, params: dict) -> gpd.GeoDataFrame:
    """`grupo`: todo retangulo que encosta se junta (o que o mapa e o relatorio mostram).
    `grupo_corte`: o mesmo, so entre as classes cortadas por retangulo, para o orcamento;
    a classe orcada pela area (`orcamento.corte_pela_area`) nao junta buracos e nao e
    paga duas vezes."""
    d = agrupar(defeitos, params["agrupamento"])
    fora = params["orcamento"].get("corte_pela_area", [])
    return agrupar(d, params["agrupamento"], "grupo_corte", fora)


def retangulos(defeitos: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Um retangulo paralelo a via por grupo (coluna `grupo`; sem ela, um por defeito).

    Colunas: grupo, ids, n, area_m2 (soma), severidade (a maior), ao_longo_m,
    transversal_m, angulo_via e o retangulo como geometria. A direcao e a do maior defeito.
    """
    colunas = ["grupo", "ids", "n", "area_m2", "severidade", "ao_longo_m", "transversal_m",
               "angulo_via"]  # fmt: skip
    if defeitos.empty:
        return gpd.GeoDataFrame({c: [] for c in colunas}, geometry=[], crs=defeitos.crs)
    d = defeitos[defeitos.geometry.notna() & ~defeitos.geometry.is_empty].copy()
    if d.empty:
        return gpd.GeoDataFrame({c: [] for c in colunas}, geometry=[], crs=defeitos.crs)
    if "grupo" not in d.columns:
        d["grupo"] = d["id"] if "id" in d.columns else range(1, len(d) + 1)
    d = d[d["grupo"].notna()]
    if "angulo_via" not in d.columns:
        d["angulo_via"] = 0.0
    d["_area"] = d.geometry.area
    linhas, geoms, angs = [], [], []
    for grupo, g in d.groupby("grupo", sort=True):
        maior = g.loc[g["_area"].idxmax()]
        sev = (
            max(g["severidade"], key=lambda s: ORDEM_SEVERIDADE.get(s, 0))
            if "severidade" in g
            else "baixa"
        )
        ids = [int(i) for i in g["id"]] if "id" in g.columns else []
        area = g["area_m2"] if "area_m2" in g.columns else g["_area"]
        linhas.append(
            {
                "grupo": int(grupo),
                "ids": ids,
                "n": len(g),
                "area_m2": round(float(area.sum()), 4),
                "severidade": sev,
                "id_maior": int(maior["id"]) if "id" in g.columns else 0,
            }
        )
        geoms.append(unary_union(list(g.geometry)))
        angs.append(float(maior["angulo_via"]) if pd.notna(maior["angulo_via"]) else 0.0)
    rets, ao_longo, transversal = caixas(np.array(geoms, dtype=object), np.array(angs))
    saida = gpd.GeoDataFrame(linhas, geometry=list(rets), crs=d.crs)
    saida["ao_longo_m"] = np.round(ao_longo, 3)
    saida["transversal_m"] = np.round(transversal, 3)
    saida["angulo_via"] = np.round(angs, 2)
    return saida


def retangulo_de_cada(defeitos: gpd.GeoDataFrame) -> gpd.GeoSeries:
    """Para cada defeito, o retangulo do grupo dele (sem `grupo`, o proprio retangulo)."""
    if "grupo" not in defeitos.columns:
        return caixas_de(defeitos)
    rets = retangulos(defeitos).set_index("grupo").geometry
    return gpd.GeoSeries(defeitos["grupo"].map(rets).values, index=defeitos.index, crs=defeitos.crs)


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
