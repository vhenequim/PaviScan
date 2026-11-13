"""Revisao manual de um levantamento: confirmar, descartar, corrigir o contorno e adicionar areas.

A revisao fica em `<run>/revisao.json`, com as geometrias no SRC do levantamento (metros),
e nunca altera o que o detector gravou
(`defeitos.geojson`): ela e aplicada por cima, na leitura. Assim da para refazer ou
apagar a revisao sem reprocessar a imagem, e o par (deteccao, revisao) vira rotulo
para treinar um modelo depois.

Situacoes de um defeito:
  pendente        ninguem olhou ainda
  confirmado      o revisor conferiu (com o contorno do detector ou com um corrigido)
  falso_positivo  nao e defeito; fica na tabela, mas sai dos indicadores por padrao
  manual          area adicionada pelo revisor, que o detector nao tinha achado
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely import STRtree
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from . import exportar, geometria, ingestao, pista

SITUACOES = ["pendente", "confirmado", "falso_positivo", "manual"]


def caminho(run: Path) -> Path:
    return Path(run) / "revisao.json"


def carregar(run: Path) -> dict:
    """Revisao gravada do levantamento (vazia se ninguem revisou)."""
    arq = caminho(run)
    if not arq.is_file():
        return {"defeitos": {}, "adicionados": []}
    rev = json.loads(arq.read_text())
    saida = {"defeitos": rev.get("defeitos", {}), "adicionados": rev.get("adicionados", [])}
    if rev.get("sem_par"):  # revisoes que nao acharam defeito ao reprocessar (ver reancorar)
        saida["sem_par"] = rev["sem_par"]
    return saida


def _juntar(a: dict, b: dict) -> dict:
    """Duas revisoes do mesmo defeito: vale a situacao da mais recente; o contorno corrigido
    de uma delas fica se a mais recente nao tiver contorno."""
    nova, velha = (a, b) if a.get("quando", "") >= b.get("quando", "") else (b, a)
    junto = dict(nova)
    if not junto.get("geometria") and velha.get("geometria"):
        junto["geometria"] = velha["geometria"]
    return junto


def reancorar(
    rev: dict, antigos: gpd.GeoDataFrame, novos: gpd.GeoDataFrame, iou_min: float
) -> tuple[dict, list[str]]:
    """Leva a revisao feita sobre `antigos` para os ids de `novos`.

    Reprocessar a imagem renumera os defeitos, e a revisao e guardada por id. Cada id
    revisado passa para o defeito novo que mais se sobrepoe ao antigo (IoU >= `iou_min`,
    comparando os contornos do detector). Revisao sem par sai de `defeitos` e vai para
    `sem_par`, com a geometria antiga, em vez de cair em outro defeito. Area manual que
    colidir com id novo ganha id acima do maior. Devolve (revisao nova, ids sem par).
    """
    # processamento anterior sem deteccao: o geojson vazio volta sem a coluna `id`
    tem_ids = len(antigos) and "id" in antigos.columns
    por_id = dict(zip(antigos["id"].astype(int), antigos.geometry, strict=True)) if tem_ids else {}
    ids_novos = novos["id"].astype(int).to_numpy() if len(novos) else np.zeros(0, dtype=int)
    arvore = STRtree(np.asarray(novos.geometry.values)) if len(novos) else None
    defeitos: dict[str, dict] = {}
    sem_par = list(rev.get("sem_par", []))
    perdidos = []
    for ident, item in rev.get("defeitos", {}).items():
        velho = por_id.get(int(ident))
        melhor, iou_melhor = None, 0.0
        if velho is not None and arvore is not None:
            for k in arvore.query(velho, predicate="intersects"):
                g = novos.geometry.iloc[int(k)]
                iou = velho.intersection(g).area / max(velho.union(g).area, 1e-12)
                if iou > iou_melhor:
                    melhor, iou_melhor = str(int(ids_novos[int(k)])), iou
        if melhor is None or iou_melhor < iou_min:
            perdidos.append(ident)
            antes = mapping(velho) if velho is not None else None
            sem_par.append({"id_antigo": int(ident), **item, "contorno_detector": antes})
            continue
        defeitos[melhor] = _juntar(defeitos[melhor], item) if melhor in defeitos else item
    teto = int(ids_novos.max()) if len(ids_novos) else 0
    adicionados = []
    for area in sorted(rev.get("adicionados", []), key=lambda a: int(a["id"])):
        area = dict(area)
        if int(area["id"]) <= teto:
            area["id"] = teto + 1
        teto = max(teto, int(area["id"]))
        adicionados.append(area)
    saida = {"defeitos": defeitos, "adicionados": adicionados}
    if sem_par:
        saida["sem_par"] = sem_par
    return saida, perdidos


def gravar(run: Path, rev: dict) -> None:
    """Grava em arquivo parcial e renomeia: uma queda no meio nao corrompe a revisao."""
    arq = caminho(run)
    parcial = arq.with_suffix(".json.parcial")
    parcial.write_text(json.dumps(rev, ensure_ascii=False, indent=1))
    os.replace(parcial, arq)


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _poligono(geometrias: list[dict]) -> BaseGeometry:
    """Une os poligonos desenhados (GeoJSON no SRC do levantamento) numa geometria valida."""
    pols = []
    for g in geometrias:
        if g.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        geom = shape(g).buffer(0)
        if not geom.is_empty and geom.area > 0:
            pols.append(geom)
    if not pols:
        raise ValueError("desenhe um poligono fechado em volta da area")
    return unary_union(pols)


def marcar(run: Path, ident: int, situacao: str) -> None:
    """Marca um defeito do detector como confirmado, falso_positivo ou de volta a pendente."""
    if situacao not in ("pendente", "confirmado", "falso_positivo"):
        raise ValueError(f"situacao invalida: {situacao}")
    rev = carregar(run)
    item = rev["defeitos"].get(str(int(ident)), {})
    if situacao == "pendente" and not item.get("geometria"):
        rev["defeitos"].pop(str(int(ident)), None)
    else:
        item.update(situacao=situacao, quando=_agora())
        rev["defeitos"][str(int(ident))] = item
    gravar(run, rev)


def corrigir(run: Path, ident: int, geometrias: list[dict]) -> None:
    """Troca o contorno de um defeito pelo desenhado (GeoJSON no SRC do levantamento).

    O defeito fica confirmado. Em area manual, troca a propria geometria dela.
    """
    geom = _poligono(geometrias)
    rev = carregar(run)
    for area in rev["adicionados"]:  # area manual: troca a propria geometria
        if int(area["id"]) == int(ident):
            area.update(geometria=mapping(geom), quando=_agora())
            gravar(run, rev)
            return
    rev["defeitos"][str(int(ident))] = {
        "situacao": "confirmado",
        "geometria": mapping(geom),
        "quando": _agora(),
    }
    gravar(run, rev)


def desfazer(run: Path, ident: int) -> None:
    """Apaga a revisao de um defeito: volta a pendente, com o contorno do detector."""
    rev = carregar(run)
    rev["defeitos"].pop(str(int(ident)), None)
    gravar(run, rev)


def adicionar(run: Path, geometrias: list[dict], classe: str, proximo_id: int) -> list[int]:
    """Acrescenta uma area manual por poligono desenhado. Devolve os ids criados.

    `proximo_id` e o primeiro id livre depois dos do detector; os ids manuais seguem dele
    e nunca sao reaproveitados.
    """
    rev = carregar(run)
    usados = [a["id"] for a in rev["adicionados"]]
    novo = max([int(proximo_id) - 1, *usados]) + 1
    criados = []
    for g in geometrias:
        geom = _poligono([g])
        rev["adicionados"].append(
            {"id": novo, "classe": classe, "geometria": mapping(geom), "quando": _agora()}
        )
        criados.append(novo)
        novo += 1
    gravar(run, rev)
    return criados


def remover(run: Path, ident: int) -> None:
    """Remove uma area manual."""
    rev = carregar(run)
    rev["adicionados"] = [a for a in rev["adicionados"] if int(a["id"]) != int(ident)]
    gravar(run, rev)


def _medidas(geom: BaseGeometry, faixas: dict) -> dict:
    largura, comprimento = geometria.lados_ret_min(geom)
    area = round(float(geom.area), 4)
    return {
        "area_m2": area,
        "largura_m": round(largura, 3),
        "comprimento_m": round(comprimento, 3),
        "severidade": geometria.severidade(area, faixas),
    }


def _local(geom: BaseGeometry, segmentos: gpd.GeoDataFrame, para_geo: Transformer) -> dict:
    """Segmento e via do centroide (o mais proximo) e coordenadas do centroide."""
    c = geom.centroid
    k = int(np.argmin(segmentos.geometry.distance(c).to_numpy()))
    lon, lat = para_geo.transform(c.x, c.y)
    return {
        "segmento": int(segmentos["segmento"].iloc[k]),
        "via": segmentos["via"].iloc[k] if "via" in segmentos.columns else "pista",
        "x": round(c.x, 2),
        "y": round(c.y, 2),
        "lon": round(lon, 7),
        "lat": round(lat, 7),
    }


def aplicar(
    defeitos: gpd.GeoDataFrame, segmentos: gpd.GeoDataFrame, rev: dict, faixas: dict
) -> gpd.GeoDataFrame:
    """Tabela de defeitos com a revisao por cima.

    Acrescenta `revisao` (uma das SITUACOES) e `corrigido` (o contorno e do revisor).
    Defeito corrigido e area manual tem area, largura, comprimento, severidade, segmento
    e coordenadas recalculados da geometria nova, em SRC projetado. Falso positivo
    continua na tabela: quem decide se ele conta e o filtro.
    """
    d = defeitos.copy()
    d["revisao"] = "pendente"
    d["corrigido"] = False
    crs = d.crs if d.crs is not None else segmentos.crs
    para_geo = Transformer.from_crs(crs, 4326, always_xy=True)
    por_id = {int(i): k for k, i in zip(d.index, d["id"], strict=True)}
    for ident, item in rev.get("defeitos", {}).items():
        k = por_id.get(int(ident))
        if k is None:  # revisao de um defeito que nao existe mais (imagem reprocessada)
            continue
        d.at[k, "revisao"] = item.get("situacao", "pendente")
        if item.get("geometria"):
            geom = shape(item["geometria"]).buffer(0)
            if geom.is_empty or geom.area <= 0:
                continue
            d.at[k, "geometry"] = geom
            d.at[k, "corrigido"] = True
            for col, v in {**_medidas(geom, faixas), **_local(geom, segmentos, para_geo)}.items():
                if col in d.columns:
                    d.at[k, col] = v
    novas = []
    for item in rev.get("adicionados", []):
        geom = shape(item["geometria"]).buffer(0)
        if geom.is_empty or geom.area <= 0:
            continue
        novas.append(
            {
                "id": int(item["id"]),
                **_medidas(geom, faixas),
                **_local(geom, segmentos, para_geo),
                "confianca": 1.0,
                "classe": item.get("classe", "panela"),
                "na_emenda": False,
                "revisao": "manual",
                "corrigido": True,
                "geometry": geom,
            }
        )
    if novas:
        extra = gpd.GeoDataFrame(novas, geometry="geometry", crs=crs)
        colunas = list(d.columns) + [c for c in extra.columns if c not in d.columns]
        d = gpd.GeoDataFrame(
            pd.concat([d, extra], ignore_index=True)[colunas], geometry="geometry", crs=crs
        )
    # caixa paralela a via: refeita para todos (contorno corrigido, area manual, run antigo)
    return geometria.com_caixa(d, pista.angulos(segmentos))


def contagem(defeitos: gpd.GeoDataFrame) -> dict[str, int]:
    """Quantos defeitos em cada situacao, mais `corrigidos` e `revisados`."""
    cont = defeitos["revisao"].value_counts() if "revisao" in defeitos.columns else {}
    c = {s: int(cont.get(s, 0)) for s in SITUACOES}
    c["corrigidos"] = int(defeitos["corrigido"].sum()) if "corrigido" in defeitos.columns else 0
    c["revisados"] = c["confirmado"] + c["falso_positivo"]
    c["do_detector"] = c["pendente"] + c["revisados"]
    return c


def recorte_do_defeito(
    run: Path, orto: Path, ident: int, geom: BaseGeometry, params: dict, caixa=None
) -> None:
    """Refaz a miniatura de um defeito com o contorno revisado: recortes/rev_<id>.jpg."""
    pasta = Path(run) / "recortes"
    pasta.mkdir(exist_ok=True)
    with ingestao.abrir(orto) as ds:
        escala = ingestao.escala_uint8(ds, params["raster"])
        destino = pasta / f"rev_{int(ident)}.jpg"
        exportar.recorte(ds, geom, destino, params["recortes"], escala, caixa)


def para_edicao(geom: BaseGeometry, vertices_max: int) -> BaseGeometry:
    """Contorno com no maximo cerca de `vertices_max` vertices, para arrastar na tela.

    O contorno do detector acompanha a borda pixel a pixel e chega a mais de mil
    vertices; cada um vira um ponto arrastavel no editor, e o mapa trava. Simplifica
    aos poucos (tolerancia de 1 cm, crescendo ate 5 m no maximo) ate caber. Os furos
    internos continuam na geometria, mas o editor desenha so o contorno externo.
    """

    def n_vertices(g: BaseGeometry) -> int:
        return sum(len(p.exterior.coords) for p in getattr(g, "geoms", [g]))

    atual, tolerancia = geom, 0.01
    while n_vertices(atual) > vertices_max and tolerancia < 5.0:
        simples = geom.simplify(tolerancia)
        if not simples.is_empty:
            atual = simples
        tolerancia *= 1.5
    return atual


def janela_de_edicao(orto: Path, limites, lado_px: int, raster_cfg: dict):
    """Imagem em resolucao cheia (reduzida ao maximo de `lado_px`) do retangulo `limites`.

    `limites` = (xmin, ymin, xmax, ymax) no SRC do levantamento. Devolve (RGB uint8 HxWx3,
    (xmin, ymin, xmax, ymax) que a imagem cobre de fato, no mesmo SRC).
    """
    from rasterio.enums import Resampling
    from rasterio.windows import Window, from_bounds
    from rasterio.windows import bounds as limites_da_janela

    with ingestao.abrir(orto) as ds:
        jan = from_bounds(*limites, ds.transform).round_offsets().round_lengths()
        jan = jan.intersection(Window(0, 0, ds.width, ds.height))
        if jan.width < 2 or jan.height < 2:
            raise ValueError("a area pedida fica fora da ortofoto")
        fator = min(1.0, lado_px / max(jan.width, jan.height))
        w, h = max(1, int(jan.width * fator)), max(1, int(jan.height * fator))
        escala = ingestao.escala_uint8(ds, raster_cfg)
        img = ds.read(
            ingestao.bandas_rgb(ds), window=jan, out_shape=(3, h, w), resampling=Resampling.average
        )
        x0, y0, x1, y1 = limites_da_janela(jan, ds.transform)
    img = np.ascontiguousarray(np.moveaxis(ingestao.para_uint8(img, escala), 0, -1))
    return img, (x0, y0, x1, y1)
