"""Detector classico: anomalia escura local, limiar robusto e filtros de forma.

Todos os limiares chegam em metros (ou niveis de cinza) pelo params.yaml e sao
convertidos para pixel com o `gsd_m` de cada chamada.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from skimage.filters import rank
from skimage.segmentation import watershed

from .base import Deteccao


@dataclass(frozen=True)
class Params:
    janela_fundo_m: float
    k_mad: float
    contraste_min: float
    area_min_m2: float
    area_max_m2: float
    solidez_min: float
    alongamento_max: float
    suavizacao_m: float
    margem_borda_m: float
    morfologia_m: float
    fundo_res_m: float
    fundo_percentil: float
    borda_nitida_m: float
    borda_suave_m: float
    contraste_cheio: float
    solidez_cheia: float
    croma_max: float
    separacao_nucleo: float


def _disco(diametro_px: int) -> np.ndarray:
    d = max(3, int(diametro_px) | 1)
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (d, d))


def _fundo(
    cinza: np.ndarray, apoio: np.ndarray, gsd_m: float, p: Params
) -> tuple[np.ndarray, np.ndarray]:
    """Asfalto sem defeito: percentil local so com os pixels de `apoio`.

    Calculado em resolucao reduzida (`fundo_res_m`) e ampliado de volta. Devolve tambem
    onde havia algum pixel de apoio dentro da janela.
    """
    h, w = cinza.shape
    f = max(1, int(round(p.fundo_res_m / gsd_m)))
    hr, wr = -(-h // f), -(-w // f)
    v = apoio.astype(np.float32)
    soma = cv2.resize(cinza.astype(np.float32) * v, (wr, hr), interpolation=cv2.INTER_AREA)
    peso = cv2.resize(v, (wr, hr), interpolation=cv2.INTER_AREA)
    red = (soma / np.maximum(peso, 1e-3)).clip(0, 255).astype(np.uint8)
    m = (peso > 0).astype(np.uint8)  # pixel reduzido vale se tiver algum apoio
    lado = max(3, int(round(p.janela_fundo_m / (gsd_m * f))) | 1)
    janela = np.ones((lado, lado), np.uint8)
    fundo_r = rank.percentile(red, janela, mask=m, p0=p.fundo_percentil / 100.0)
    tem_r = cv2.dilate(m, janela)
    fundo = cv2.resize(fundo_r, (w, h), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    tem = cv2.resize(tem_r, (w, h), interpolation=cv2.INTER_NEAREST) > 0
    return fundo, tem


def _limiar(valores: np.ndarray, p: Params) -> float:
    """max(piso, mediana + k x MAD). Otsu nao serve: inventa classe em pista limpa."""
    med = float(np.median(valores))
    mad = float(np.median(np.abs(valores - med)))
    return max(float(p.contraste_min), med + p.k_mad * mad)


def _forma(parte: np.ndarray) -> tuple[float, float]:
    """Solidez (area / area do fecho convexo) e alongamento (lado maior / lado menor)."""
    contornos, _ = cv2.findContours(
        parte.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
    )
    c = max(contornos, key=cv2.contourArea)
    area_fecho = cv2.contourArea(cv2.convexHull(c))
    solidez = cv2.contourArea(c) / area_fecho if area_fecho > 0 else 0.0
    (lw, lh) = cv2.minAreaRect(c)[1]
    lw, lh = lw + 1.0, lh + 1.0  # contorno passa pelo centro do pixel
    return float(solidez), float(max(lw, lh) / min(lw, lh))


def _separar(parte: np.ndarray, fracao_nucleo: float) -> list[np.ndarray]:
    """Watershed so quando a regiao tem mais de um nucleo.

    Nucleo = onde a distancia ate a borda passa de `fracao_nucleo` da maior distancia da
    regiao. Dois buracos colados por um estrangulamento dao dois nucleos; um buraco so,
    mesmo recortado, da um.
    """
    dist = cv2.distanceTransform(parte.astype(np.uint8), cv2.DIST_L2, 5)
    nucleos = (dist >= fracao_nucleo * float(dist.max())).astype(np.uint8)
    n, marcas = cv2.connectedComponents(nucleos, connectivity=8)
    if n <= 2:
        return [parte]
    rot = watershed(-dist, marcas, mask=parte)
    return [rot == i for i in range(1, n) if (rot == i).any()]


class DetectorClassico:
    """Anomalia escura local. Sem estado entre chamadas."""

    nome = "classico"
    versao = "0.3.0"
    limites = (
        "O detector procura anomalias escuras locais no asfalto, sem aprendizado. Mancha de "
        "oleo, remendo escuro, tampa de bueiro, poca, sarjeta, sombra pequena e veiculo escuro "
        "aparecem como buraco. Use a confianca minima e a conferencia visual para separar os casos."
    )

    def __init__(self, **params: float) -> None:
        self.p = Params(**params)

    def detectar(self, img: np.ndarray, gsd_m: float, valida: np.ndarray) -> list[Deteccao]:
        p = self.p
        img = np.ascontiguousarray(img)
        # asfalto e cinza: pixel colorido (vegetacao, veiculo, pintura, solo) sai da analise
        croma = img.max(axis=2).astype(np.int16) - img.min(axis=2)
        valida = np.asarray(valida, dtype=bool) & (croma <= p.croma_max)
        px2 = gsd_m * gsd_m
        if valida.sum() * px2 < p.area_min_m2:
            return []

        # 1. cinza com desfoque leve
        cinza = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        sigma = p.suavizacao_m / gsd_m
        if sigma > 0:
            cinza = cv2.GaussianBlur(cinza, (0, 0), sigma)
        cinza_f = cinza.astype(np.float32)

        # miolo da pista: longe alguns centimetros da borda
        r_borda = int(round(p.margem_borda_m / gsd_m))
        miolo = valida
        if r_borda > 0:
            miolo = cv2.erode(valida.astype(np.uint8), _disco(2 * r_borda + 1)) > 0
        if not miolo.any():
            return []

        # 2 e 3. fundo local e limiar robusto, em duas passadas: a segunda estima o
        # fundo sem os pixels ja anomalos, para o buraco grande nao puxar a mediana
        d_morf = int(round(p.morfologia_m / gsd_m))
        fundo, _ = _fundo(cinza, valida, gsd_m, p)
        anomalia = fundo - cinza_f
        limiar = _limiar(anomalia[miolo], p)
        bruto = (anomalia > limiar) & miolo
        if not bruto.any():
            return []
        suspeito = cv2.dilate(bruto.astype(np.uint8), _disco(2 * d_morf + 1)) > 0
        fundo2, tem = _fundo(cinza, valida & ~suspeito, gsd_m, p)
        fundo = np.where(tem, fundo2, fundo)
        anomalia = fundo - cinza_f
        limiar = _limiar(
            anomalia[miolo & ~suspeito] if (miolo & ~suspeito).any() else anomalia[miolo], p
        )

        # 4. limpeza morfologica e componentes conexos
        el = _disco(d_morf)
        binaria = ((anomalia > limiar) & miolo).astype(np.uint8)
        binaria = cv2.morphologyEx(binaria, cv2.MORPH_OPEN, el)
        binaria = cv2.morphologyEx(binaria, cv2.MORPH_CLOSE, el)
        binaria[~valida] = 0
        n, rotulos, stats, _ = cv2.connectedComponentsWithStats(binaria, connectivity=8)
        if n <= 1:
            return []

        gx = cv2.Sobel(cinza_f, cv2.CV_32F, 1, 0, ksize=3) / 8.0
        gy = cv2.Sobel(cinza_f, cv2.CV_32F, 0, 1, ksize=3) / 8.0
        grad = cv2.magnitude(gx, gy)

        saida: list[Deteccao] = []
        for i in range(1, n):
            x, y, w, h, area_px = stats[i]
            if area_px * px2 < p.area_min_m2:
                continue
            comp = rotulos[y : y + h, x : x + w] == i
            partes = [comp]
            if _forma(comp)[0] < p.solidez_min:
                partes = _separar(comp, p.separacao_nucleo)
            for parte in partes:
                # 5. filtros de forma, em metros
                area_m2 = float(parte.sum()) * px2
                if not (p.area_min_m2 <= area_m2 <= p.area_max_m2):
                    continue
                solidez, alongamento = _forma(parte)
                if solidez < p.solidez_min or alongamento > p.alongamento_max:
                    continue
                # 6. confianca: contraste contra o entorno, nitidez da borda e forma compacta
                rec = (slice(y, y + h), slice(x, x + w))
                contraste = float(anomalia[rec][parte].mean())
                c_contraste = np.clip(contraste / (p.contraste_cheio * limiar), 0.0, 1.0)
                borda = parte & ~(cv2.erode(parte.astype(np.uint8), _disco(3)) > 0)
                g = float(grad[rec][borda].mean()) if borda.any() else 0.0
                transicao_m = contraste / max(g, 1e-6) * gsd_m
                c_borda = np.clip(
                    (p.borda_suave_m - transicao_m) / (p.borda_suave_m - p.borda_nitida_m),
                    0.0,
                    1.0,
                )
                c_forma = np.clip(
                    (solidez - p.solidez_min) / (p.solidez_cheia - p.solidez_min), 0.0, 1.0
                )
                confianca = float(c_contraste + c_borda + c_forma) / 3.0
                mascara = np.zeros(valida.shape, dtype=bool)
                mascara[rec] = parte
                saida.append(Deteccao(mascara, round(confianca, 3)))
        return saida
