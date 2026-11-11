"""Pista automatica: eixos de via do OpenStreetMap + cor do asfalto na ortofoto.

Serve quando ninguem desenhou o poligono da pista. Cada trecho reto de via vira uma
feicao com `nome`. Dados de via (c) colaboradores do OpenStreetMap, licenca ODbL.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import cv2
import geopandas as gpd
import numpy as np
from affine import Affine
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.features import rasterize, shapes
from shapely.geometry import LineString, box, shape
from shapely.ops import linemerge, unary_union

from . import config as config_mod
from . import ingestao

SERVIDOR = "https://overpass-api.de/api/interpreter"  # instancia oficial do Overpass


def baixar_vias(sul: float, oeste: float, norte: float, leste: float, tentativas: int = 5) -> dict:
    """Vias (`highway`) do retangulo, pela API Overpass. Repete com espera crescente.

    A consulta envia ao servidor do OpenStreetMap o retangulo geografico da imagem.
    """
    consulta = f'[out:json][timeout:50];way["highway"]({sul},{oeste},{norte},{leste});out geom;'
    corpo = urllib.parse.urlencode({"data": consulta}).encode()
    erro: Exception | None = None
    for t in range(tentativas):
        req = urllib.request.Request(SERVIDOR, data=corpo, headers={"User-Agent": "paviscan"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            erro = e  # servidor cheio ou fora do ar e comum: espera e tenta de novo
            time.sleep(5 * (t + 1))
    raise RuntimeError(f"Overpass (OpenStreetMap) nao respondeu: {erro}")


def _partir_nas_dobras(linha: LineString, dobra_max: float, trecho_min: float) -> list[LineString]:
    """Parte a linha em trechos aproximadamente retos, para o eixo do trecho fazer sentido."""
    pts = list(linha.coords)
    trechos, atual = [], [pts[0], pts[1]]
    rumo0 = math.atan2(pts[1][1] - pts[0][1], pts[1][0] - pts[0][0])
    for a, b in zip(pts[1:], pts[2:], strict=False):
        rumo = math.atan2(b[1] - a[1], b[0] - a[0])
        dobra = abs(math.degrees((rumo - rumo0 + math.pi) % (2 * math.pi) - math.pi))
        if dobra > dobra_max and LineString(atual).length >= trecho_min:
            trechos.append(atual)
            atual, rumo0 = [a, b], rumo
        else:
            atual.append(b)
    if trechos and LineString(atual).length < trecho_min:
        trechos[-1] = trechos[-1] + atual[1:]
    else:
        trechos.append(atual)
    return [LineString(t) for t in trechos]


def _eixos(osm: dict, crs, quadro, cfg: dict) -> list[tuple[str, str, LineString]]:
    """(nome, classe, linha) no SRC do raster, um por trecho reto dentro do quadro."""
    para_raster = Transformer.from_crs(4326, crs, always_xy=True)
    classes = set(cfg["classes"])
    por_nome: dict[tuple[str, str], list[LineString]] = {}
    sem_nome = 0
    for e in osm.get("elements", []):
        tags = e.get("tags", {})
        classe = str(tags.get("highway", "")).removesuffix("_link")
        if e.get("type") != "way" or classe not in classes or len(e.get("geometry", [])) < 2:
            continue
        xs, ys = para_raster.transform(
            [p["lon"] for p in e["geometry"]], [p["lat"] for p in e["geometry"]]
        )
        linha = LineString(list(zip(xs, ys, strict=True))).intersection(quadro)
        if linha.is_empty:
            continue
        nome = tags.get("name")
        if not nome:
            sem_nome += 1
            nome = f"Via sem nome {sem_nome}"
        partes = linha.geoms if hasattr(linha, "geoms") else [linha]
        por_nome.setdefault((nome, classe), []).extend(
            p for p in partes if p.geom_type == "LineString"
        )
    eixos = []
    for (nome, classe), linhas in por_nome.items():
        unida = linemerge(linhas) if len(linhas) > 1 else linhas[0]
        k = 0
        for parte in unida.geoms if hasattr(unida, "geoms") else [unida]:
            if parte.length < cfg["trecho_min_m"]:
                continue
            for trecho in _partir_nas_dobras(parte, cfg["dobra_max_graus"], cfg["trecho_min_m"]):
                k += 1
                eixos.append((nome if k == 1 else f"{nome} ({k})", classe, trecho))
    ordem = {c: i for i, c in enumerate(cfg["classes"])}
    return sorted(eixos, key=lambda e: (ordem.get(e[1], 99), -e[2].length))


def _eixos_de_arquivo(caminho: Path, crs, quadro, cfg: dict) -> list[tuple[str, str, LineString]]:
    """Eixos desenhados pelo usuario (GeoJSON de linhas), partidos em trechos retos."""
    g = gpd.read_file(caminho)
    if g.crs is None:
        raise ValueError(f"{caminho} nao tem SRC")
    g = g.to_crs(crs)
    col = next((c for c in ("nome", "name") if c in g.columns), None)
    eixos = []
    for i, geom in enumerate(g.geometry):
        if geom is None or geom.is_empty:
            continue
        nome = str(g[col].iloc[i]) if col and str(g[col].iloc[i]) not in ("", "nan", "None") else ""
        nome = nome or f"Via {i + 1}"
        dentro = geom.intersection(quadro)
        for parte in dentro.geoms if hasattr(dentro, "geoms") else [dentro]:
            if parte.geom_type != "LineString" or parte.length < cfg["trecho_min_m"]:
                continue
            for trecho in _partir_nas_dobras(parte, cfg["dobra_max_graus"], cfg["trecho_min_m"]):
                eixos.append((nome, "desenhado", trecho))
    return eixos


def _tapar_furos(m: np.ndarray, area_max_px: float) -> np.ndarray:
    """Furo interno pequeno (buraco, bueiro, pintura) volta a ser pista."""
    n, rot, stats, _ = cv2.connectedComponentsWithStats((~m).astype(np.uint8), connectivity=4)
    h, w = m.shape
    saida = m.copy()
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        na_moldura = x == 0 or y == 0 or x + bw == w or y + bh == h
        if area <= area_max_px and not na_moldura:
            saida[rot == i] = True
    return saida


def gerar(
    orto: Path,
    saida: Path,
    config: Path | None = None,
    vias: Path | None = None,
    eixo: Path | None = None,
    superficie: str = "asfalto",
) -> Path:
    """Grava `saida` (GeoJSON no SRC da ortofoto) com um poligono de pista por trecho de via.

    Os eixos vem de `eixo` (linhas desenhadas pelo usuario) ou, sem ele, do OpenStreetMap.
    `superficie` diz como achar a borda real em volta do eixo: "asfalto" (cinza, com brilho
    parecido com o do eixo) ou "terra" (tudo que nao e vegetacao: serve para via de terra
    ou de revestimento muito gasto).
    """
    if superficie not in ("asfalto", "terra"):
        raise ValueError(f"superficie desconhecida: {superficie}")
    orto, saida = Path(orto), Path(saida)
    params = config_mod.carregar(config)
    cfg = params["pista_osm"]
    with ingestao.abrir(orto) as ds:
        crs, limites = ds.crs, ds.bounds
        fator = max(1.0, cfg["res_m"] / ingestao.gsd_m(ds))
        h, w = max(1, round(ds.height / fator)), max(1, round(ds.width / fator))
        escala = ingestao.escala_uint8(ds, params["raster"])
        bruto = ds.read(ingestao.bandas_rgb(ds), out_shape=(3, h, w), resampling=Resampling.average)
        rgb = np.moveaxis(ingestao.para_uint8(bruto, escala), 0, -1)
        com_dado = ds.dataset_mask(out_shape=(h, w)) > 0
        if com_dado.all():  # ortofoto sem mascara: a moldura lisa nao e dado
            com_dado &= ~ingestao.moldura_lisa(rgb.astype(np.uint8), params["moldura"])
        tf = ds.transform @ Affine.scale(ds.width / w, ds.height / h)
    res = (abs(tf.a) + abs(tf.e)) / 2

    if eixo is not None:
        eixos, fonte = _eixos_de_arquivo(Path(eixo), crs, box(*limites), cfg), "eixo desenhado"
        if not eixos:
            raise RuntimeError("o eixo desenhado nao cruza a ortofoto ou e curto demais")
    else:
        cache = Path(vias) if vias else saida.with_name("vias_osm.json")
        if cache.is_file():
            osm = json.loads(cache.read_text())
        else:
            para_geo = Transformer.from_crs(crs, 4326, always_xy=True)
            lons, lats = para_geo.transform(
                [limites.left, limites.right, limites.left, limites.right],
                [limites.bottom, limites.bottom, limites.top, limites.top],
            )
            osm = baixar_vias(min(lats), min(lons), max(lats), max(lons))
            saida.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(osm))
        eixos, fonte = _eixos(osm, crs, box(*limites), cfg), "OpenStreetMap"
        if not eixos:
            raise RuntimeError("OpenStreetMap nao tem via mapeada dentro da ortofoto")

    hsv = cv2.cvtColor(np.ascontiguousarray(rgb.astype(np.uint8)), cv2.COLOR_RGB2HSV)
    sat, brilho = hsv[..., 1] / 255.0, hsv[..., 2] / 255.0
    cinzento = (sat <= cfg["saturacao_max"]) & com_dado
    cor = rgb.astype(np.int16)
    sem_vegetacao = ((2 * cor[..., 1] - cor[..., 0] - cor[..., 2]) <= cfg["exg_max"]) & com_dado
    # telhado, laje e concreto claros nao sao via: claro e sem cor
    claro = (brilho >= cfg["brilho_claro"]) & (sat <= cfg["saturacao_max"])
    sem_vegetacao &= ~claro

    def raster(geom) -> np.ndarray:
        return rasterize([geom], out_shape=(h, w), transform=tf, fill=0).astype(bool)

    larguras = cfg["meia_largura_m"]
    corredores, asfalto, miolo = [], np.zeros((h, w), bool), np.zeros((h, w), bool)
    for _, classe, linha in eixos:
        corredor = raster(linha.buffer(larguras.get(classe, larguras["padrao"]), cap_style="flat"))
        perto = raster(linha.buffer(cfg["faixa_referencia_m"], cap_style="flat"))
        corredores.append(corredor)
        if superficie == "terra":  # a via e tudo que nao e vegetacao em volta do eixo
            asfalto |= corredor & sem_vegetacao
            miolo |= perto
            continue
        ref = brilho[perto & cinzento]
        if ref.size < cfg["pixels_referencia_min"]:
            continue
        # brilho do asfalto medido no proprio eixo da via; a borda real vem da cor
        v0 = float(np.median(ref))
        asfalto |= corredor & cinzento & (np.abs(brilho - v0) <= cfg["tolerancia_brilho"])
        miolo |= perto

    d = max(3, int(round(cfg["limpeza_m"] / res)) | 1)
    el = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (d, d))
    asfalto = cv2.morphologyEx(asfalto.astype(np.uint8), cv2.MORPH_OPEN, el)
    n, rot = cv2.connectedComponents(asfalto, connectivity=4)
    ligados = np.unique(rot[miolo & (rot > 0)])  # so o que encosta num eixo e via
    asfalto = _tapar_furos(np.isin(rot, ligados) & (rot > 0), cfg["buraco_max_m2"] / res**2)

    dono = np.zeros((h, w), np.int32)
    for i, corredor in enumerate(corredores, start=1):
        dono[corredor & asfalto & (dono == 0)] = i
    partes: dict[int, list] = {}
    for geom, valor in shapes(dono, mask=dono > 0, transform=tf):
        partes.setdefault(int(valor), []).append(shape(geom))
    linhas = []
    for i, pols in sorted(partes.items()):
        pol = unary_union(pols).simplify(res / 2).buffer(0)
        if pol.area < cfg["area_min_m2"]:
            continue
        nome, classe, _ = eixos[i - 1]
        linhas.append({"nome": nome, "classe": classe, "fonte": fonte, "geometry": pol})
    if not linhas:
        raise RuntimeError("nenhum trecho de pista encontrado em volta dos eixos")
    saida.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(linhas, geometry="geometry", crs=crs).to_file(saida, driver="GeoJSON")
    return saida
