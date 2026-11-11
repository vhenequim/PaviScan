"""Saidas do levantamento: GeoJSON, GPKG, XLSX, previa.png e recortes dos buracos."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import ColorInterp, Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window, from_bounds
from rasterio.windows import transform as transform_da_janela

from . import ingestao


def gravar_geojson(gdf: gpd.GeoDataFrame, caminho: Path) -> None:
    """GeoJSON no SRC do proprio GeoDataFrame (UTM), inclusive quando vazio."""
    if len(gdf):
        gdf.to_file(caminho, driver="GeoJSON")
        return
    epsg = gdf.crs.to_epsg() if gdf.crs is not None else None
    crs = {"type": "name", "properties": {"name": f"urn:ogc:def:crs:EPSG::{epsg}"}}
    fc = {"type": "FeatureCollection", "features": []}
    if epsg:
        fc["crs"] = crs
    Path(caminho).write_text(json.dumps(fc))


def previa(
    orto: Path, saida: Path, lado_max_px: int, moldura: dict, escala: float = 1.0
) -> list[list[float]]:
    """Grava previa.png alinhada a EPSG:4326 e devolve [[lat_s, lon_o], [lat_n, lon_l]].

    O lado maior fica em no maximo `lado_max_px`. `escala` leva raster nao uint8 a 0-255.
    """
    with rasterio.open(orto) as src:
        com_alfa = ingestao.tem_alfa(src)
        sem_mascara = ingestao.sem_mascara(src)
        bandas = ingestao.bandas_rgb(src)
        canto = ingestao.para_uint8(src.read(bandas, window=Window(0, 0, 1, 1)), escala)[:, 0, 0]
        with WarpedVRT(
            src, crs="EPSG:4326", resampling=Resampling.bilinear, add_alpha=not com_alfa
        ) as vrt:
            # se a origem ja tem alfa, o VRT a preserva; senao, a banda nova e a ultima
            i_alfa = src.colorinterp.index(ColorInterp.alpha) + 1 if com_alfa else vrt.count
            fator = min(1.0, lado_max_px / max(vrt.width, vrt.height))
            w, h = max(1, int(vrt.width * fator)), max(1, int(vrt.height * fator))
            dados = vrt.read([*bandas, i_alfa], out_shape=(4, h, w), resampling=Resampling.average)
            b = vrt.bounds
    rgb = np.ascontiguousarray(np.moveaxis(ingestao.para_uint8(dados[:3], escala), 0, -1))
    alfa = np.where(dados[3] > 0, 255, 0).astype(np.uint8)
    if sem_mascara:  # ortofoto gravada sem mascara: a moldura lisa vira transparente
        # a sobra da reprojecao (e a franja de 1 a 2 px em volta dela) conta como moldura
        lado = 2 * int(moldura["franja_px"]) + 1
        sobra = cv2.dilate((alfa == 0).astype(np.uint8), np.ones((lado, lado), np.uint8)) > 0
        pintada = rgb.copy()
        pintada[sobra] = canto
        alfa[ingestao.moldura_lisa(pintada, moldura)] = 0
    rgba = np.dstack([rgb, alfa])
    cv2.imwrite(str(saida / "previa.png"), cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
    return [[b.bottom, b.left], [b.top, b.right]]


def recorte(ds, geom, destino: Path, cfg: dict, escala: float = 1.0) -> bool:
    """Grava em `destino` a miniatura de um defeito, com o contorno desenhado."""
    tudo = Window(0, 0, ds.width, ds.height)
    x0, y0, x1, y1 = geom.bounds
    lado = max(x1 - x0, y1 - y0) + 2 * cfg["margem_m"]
    lado = max(lado, cfg["lado_min_m"])
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    jan = from_bounds(cx - lado / 2, cy - lado / 2, cx + lado / 2, cy + lado / 2, ds.transform)
    jan = jan.round_offsets().round_lengths().intersection(tudo)
    if jan.width < 2 or jan.height < 2:
        return False
    fator = int(cfg["lado_px"]) / max(jan.width, jan.height)
    w, h = max(1, int(jan.width * fator)), max(1, int(jan.height * fator))
    img = ds.read(
        ingestao.bandas_rgb(ds), window=jan, out_shape=(3, h, w), resampling=Resampling.bilinear
    )
    img = np.ascontiguousarray(np.moveaxis(ingestao.para_uint8(img, escala), 0, -1))
    inv = ~(transform_da_janela(jan, ds.transform) @ rasterio.Affine.scale(1 / fator))
    for parte in geom.geoms if hasattr(geom, "geoms") else [geom]:
        pts = np.array([inv @ xy for xy in parte.exterior.coords]).round().astype(np.int32)
        cv2.polylines(img, [pts], True, (255, 40, 40), 1, cv2.LINE_AA)
    cv2.imwrite(str(destino), cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
    return True


def recortes(ds, defeitos: gpd.GeoDataFrame, saida: Path, cfg: dict, escala: float = 1.0) -> int:
    """Uma miniatura por buraco em recortes/<id>.jpg, com o contorno desenhado."""
    pasta = saida / "recortes"
    pasta.mkdir(exist_ok=True)
    for velho in pasta.glob("*.jpg"):
        velho.unlink()
    if defeitos.empty:
        return 0
    escolhidos = defeitos.sort_values("area_m2", ascending=False).head(int(cfg["maximo"]))
    for ident, geom in zip(escolhidos["id"], escolhidos.geometry, strict=True):
        recorte(ds, geom, pasta / f"{int(ident)}.jpg", cfg, escala)
    return len(escolhidos)


def planilha(defeitos: gpd.GeoDataFrame, segmentos: gpd.GeoDataFrame, resumo: dict) -> dict:
    """Abas do XLSX: Resumo, Buracos, Segmentos (sem geometria)."""
    linhas = [
        (k, json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
        for k, v in resumo.items()
    ]
    return {
        "Resumo": pd.DataFrame(linhas, columns=["indicador", "valor"]),
        "Buracos": pd.DataFrame(defeitos.drop(columns="geometry")),
        "Segmentos": pd.DataFrame(segmentos.drop(columns="geometry")),
    }


def gravar_xlsx(abas: dict, destino) -> None:
    with pd.ExcelWriter(destino, engine="openpyxl") as xl:
        for nome, tabela in abas.items():
            tabela.to_excel(xl, sheet_name=nome, index=False)


def gravar(saida: Path, defeitos: gpd.GeoDataFrame, segmentos: gpd.GeoDataFrame, resumo: dict):
    """defeitos.geojson, segmentos.geojson, resumo.json, defeitos.gpkg e resumo.xlsx."""
    gravar_geojson(defeitos, saida / "defeitos.geojson")
    gravar_geojson(segmentos, saida / "segmentos.geojson")
    (saida / "resumo.json").write_text(json.dumps(resumo, indent=2, ensure_ascii=False))
    gpkg = saida / "defeitos.gpkg"
    gpkg.unlink(missing_ok=True)
    segmentos.to_file(gpkg, layer="segmentos", driver="GPKG")
    if len(defeitos):
        defeitos.to_file(gpkg, layer="defeitos", driver="GPKG")
    gravar_xlsx(planilha(defeitos, segmentos, resumo), saida / "resumo.xlsx")
