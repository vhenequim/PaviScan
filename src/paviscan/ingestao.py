"""Abre a ortofoto em SRC metrico e le por janelas. Nunca o raster inteiro."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import cv2
import numpy as np
import rasterio
from affine import Affine
from pyproj import CRS, Transformer
from rasterio.enums import ColorInterp, MaskFlags, Resampling
from rasterio.features import shapes
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window
from shapely.geometry import Polygon, box, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union


def utm_do_ponto(lon: float, lat: float) -> CRS:
    """Zona UTM do ponto. SIRGAS 2000 no Brasil (hemisferio sul), WGS 84 no resto."""
    zona = int((lon + 180) // 6) + 1
    if lat < 0 and 17 <= zona <= 25 and -74 < lon < -28:
        return CRS.from_epsg(31960 + zona)
    return CRS.from_epsg((32700 if lat < 0 else 32600) + zona)


def _precisa_reprojetar(crs: CRS) -> bool:
    if not crs.is_projected:
        return True
    if crs.to_epsg() == 3857:  # pseudo-Mercator nao mede em metro de verdade
        return True
    return crs.axis_info[0].unit_name not in ("metre", "meter")


def tem_alfa(ds) -> bool:
    return ColorInterp.alpha in ds.colorinterp


def sem_mascara(ds) -> bool:
    """True quando o raster nao traz nodata, alfa nem mascara: todo pixel conta como dado."""
    return all(MaskFlags.all_valid in f for f in ds.mask_flag_enums)


def bandas_rgb(ds) -> list[int]:
    """Indices das bandas de cor. Imagem de uma banda e repetida nos tres canais."""
    n_cor = ds.count - (1 if tem_alfa(ds) else 0)
    return [1, 2, 3] if n_cor >= 3 else [1, 1, 1]


@contextmanager
def abrir(caminho: Path) -> Iterator[rasterio.io.DatasetReader]:
    """Dataset em SRC projetado em metros. Entrada em graus vira UTM por WarpedVRT."""
    with rasterio.open(caminho) as src:
        if src.crs is None:
            raise ValueError(f"{caminho} nao tem SRC")
        crs = CRS.from_user_input(src.crs)
        if not _precisa_reprojetar(crs):
            yield src
            return
        b = src.bounds
        para_geo = Transformer.from_crs(crs, 4326, always_xy=True)
        lon, lat = para_geo.transform((b.left + b.right) / 2, (b.bottom + b.top) / 2)
        utm = utm_do_ponto(lon, lat)
        # com alfa, a sobra da reprojecao sai como "sem dado" em vez de preto valido
        with WarpedVRT(
            src, crs=utm.to_wkt(), resampling=Resampling.bilinear, add_alpha=not tem_alfa(src)
        ) as vrt:
            yield vrt


def gsd_m(ds) -> float:
    """Tamanho do pixel em metros (media dos dois eixos)."""
    return float((abs(ds.res[0]) + abs(ds.res[1])) / 2)


def janelas(largura: int, altura: int, tamanho: int, sobreposicao: int) -> Iterator[Window]:
    """Janelas de `tamanho` px com `sobreposicao` px entre vizinhas, cobrindo o raster."""
    passo = max(1, tamanho - sobreposicao)
    for lin in range(0, max(altura - sobreposicao, 1), passo):
        for col in range(0, max(largura - sobreposicao, 1), passo):
            yield Window(col, lin, min(tamanho, largura - col), min(tamanho, altura - lin))


def escala_uint8(ds, cfg: dict) -> float:
    """Fator que leva os valores do raster para 0 a 255. Um so para a imagem inteira.

    Normalizar janela a janela faria o mesmo asfalto mudar de brilho entre vizinhas.
    `cfg` e o bloco `raster` do params.yaml: o percentil `percentil_branco` de uma
    amostra reduzida (lado de `amostra_px`) vira 255.
    """
    if ds.dtypes[0] == "uint8":
        return 1.0
    fator = max(1, max(ds.width, ds.height) // int(cfg["amostra_px"]))
    h, w = max(1, ds.height // fator), max(1, ds.width // fator)
    amostra = ds.read(bandas_rgb(ds), out_shape=(3, h, w))
    valores = amostra[:, ds.dataset_mask(out_shape=(h, w)) > 0]
    topo = float(np.percentile(valores, cfg["percentil_branco"])) if valores.size else 0.0
    return 255.0 / topo if topo > 0 else 1.0


def para_uint8(dados: np.ndarray, escala: float) -> np.ndarray:
    if dados.dtype == np.uint8:
        return dados
    return (dados.astype(np.float32) * escala).clip(0, 255).astype(np.uint8)


def ler_rgb(ds, janela: Window, escala: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """RGB uint8 HxWx3 e mascara bool HxW dos pixels com dado (nodata e alfa fora)."""
    dados = para_uint8(ds.read(bandas_rgb(ds), window=janela), escala)
    com_dado = ds.dataset_mask(window=janela) > 0
    return np.ascontiguousarray(np.moveaxis(dados, 0, -1)), com_dado


def area_com_dado(ds, res_m: float) -> BaseGeometry:
    """Poligono da parte da ortofoto que tem dado, medido em resolucao reduzida."""
    if sem_mascara(ds):
        return box(*ds.bounds)
    fator = max(1.0, res_m / gsd_m(ds))
    h, w = max(1, round(ds.height / fator)), max(1, round(ds.width / fator))
    m = (ds.dataset_mask(out_shape=(h, w)) > 0).astype(np.uint8)
    tf = ds.transform @ Affine.scale(ds.width / w, ds.height / h)
    pols = [shape(g) for g, v in shapes(m, mask=m > 0, transform=tf) if v]
    return unary_union(pols) if pols else Polygon()


def moldura_lisa(rgb: np.ndarray, cfg: dict) -> np.ndarray:
    """Moldura lisa (branca ou preta) ligada aos cantos, em ortofoto gravada sem mascara.

    Devolve bool HxW, True onde e moldura. Usar em imagem reduzida, nunca por janela.
    `cfg` e o bloco `moldura` do params.yaml.
    """
    h, w = rgb.shape[:2]
    img = np.ascontiguousarray(rgb[..., :3])
    fora = np.zeros((h + 2, w + 2), np.uint8)
    modo = 4 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8)
    tol = (int(cfg["tolerancia"]),) * 3
    for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        cor = img[y, x].astype(int)
        lisa = cor.min() >= cfg["branco_min"] or cor.max() <= cfg["preto_max"]
        if fora[y + 1, x + 1] or not lisa:
            continue
        cv2.floodFill(img, fora, (x, y), 0, tol, tol, modo)
    return fora[1:-1, 1:-1] > 0
