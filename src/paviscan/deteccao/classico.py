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
    solo_exposto: bool
    solo_contraste_min: float
    solo_quente_min: float
    solo_matiz_max: float
    exg_max: float
    solo_sobreposicao_max: float
    solo_janela_fundo_m: float
    solo_fundo_percentil: float
    solo_k_mad: float
    solo_peso_brilho: float


def _disco(diametro_px: int) -> np.ndarray:
    d = max(3, int(diametro_px) | 1)
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (d, d))


def _fundo(
    cinza: np.ndarray,
    apoio: np.ndarray,
    gsd_m: float,
    p: Params,
    janela_m: float | None = None,
    percentil: float | None = None,
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
    lado = max(3, int(round((janela_m or p.janela_fundo_m) / (gsd_m * f))) | 1)
    janela = np.ones((lado, lado), np.uint8)
    p0 = (p.fundo_percentil if percentil is None else percentil) / 100.0
    fundo_r = rank.percentile(red, janela, mask=m, p0=p0)
    tem_r = cv2.dilate(m, janela)
    fundo = cv2.resize(fundo_r, (w, h), interpolation=cv2.INTER_LINEAR).astype(np.float32)
    tem = cv2.resize(tem_r, (w, h), interpolation=cv2.INTER_NEAREST) > 0
    return fundo, tem


def _limiar(valores: np.ndarray, piso: float, k_mad: float) -> float:
    """max(piso, mediana + k x MAD). Otsu nao serve: inventa classe em pista limpa."""
    med = float(np.median(valores))
    mad = float(np.median(np.abs(valores - med)))
    return max(float(piso), med + k_mad * mad)


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
    """Anomalia local contra o pavimento em volta. Sem estado entre chamadas.

    Duas passadas com a mesma receita: buraco escuro (sinal = cinza) e revestimento
    perdido com solo exposto (sinal = quao "quente" e a cor, R - B).
    """

    nome = "classico"
    versao = "0.5.2"
    limites = (
        "O detector compara cada ponto com o pavimento em volta, sem aprendizado. Procura duas "
        "coisas: anomalia escura (buraco) e cor de solo no meio do pavimento (revestimento "
        "perdido). Mancha de oleo, remendo escuro, tampa de bueiro, poca, sarjeta, sombra pequena "
        "e veiculo escuro aparecem como buraco; lama, areia e terra trazida por roda aparecem como "
        "revestimento perdido, assim como veiculo de cor quente e folha seca. O revestimento "
        "perdido e medido contra o pavimento inteiro que sobrou em volta (janela de 6 m): trecho "
        "que perdeu a capa na largura toda nao tem com o que comparar e pode sair como bom. Use a "
        "confianca minima e a revisao manual para separar os casos."
    )

    def __init__(self, **params: float) -> None:
        self.p = Params(**params)

    def detectar(self, img: np.ndarray, gsd_m: float, valida: np.ndarray) -> list[Deteccao]:
        p = self.p
        img = np.ascontiguousarray(img)
        valida = np.asarray(valida, dtype=bool)
        # 1a passada, buraco escuro. Asfalto e cinza: pixel colorido (vegetacao, veiculo,
        # pintura, solo) sai da analise
        croma = img.max(axis=2).astype(np.int16) - img.min(axis=2)
        cinza = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        saida = self._passada(
            cinza, valida & (croma <= p.croma_max), gsd_m, p.contraste_min, "panela"
        )
        if not p.solo_exposto:
            return saida
        # 2a passada, revestimento perdido: onde a capa saiu aparece base ou solo, mais claro
        # e mais "quente" (R - B alto) que o pavimento em volta. Forma faixas longas e
        # irregulares, entao mede-se a area, sem filtro de forma, contra o pavimento mais
        # escuro de uma janela larga. Vegetacao (verde dominante) e pintura (clara, mas sem
        # cor) ficam fora
        r, g, b = (img[..., k].astype(np.int16) for k in range(3))
        quente = r - b
        solo = np.clip(p.solo_peso_brilho * cinza + quente, 0, 255).astype(np.uint8)
        sem_vegetacao = (2 * g - r - b) <= p.exg_max
        # solo e terra ficam entre o vermelho e o laranja; pintura amarela tem matiz maior
        matiz = 2.0 * cv2.cvtColor(img, cv2.COLOR_RGB2HSV)[..., 0]
        novas = self._passada(
            255 - solo,
            valida & sem_vegetacao,
            gsd_m,
            p.solo_contraste_min,
            "solo_exposto",
            extensa=True,
            porta=(quente >= p.solo_quente_min) & (matiz <= p.solo_matiz_max),
        )
        ocupado = np.zeros(valida.shape, dtype=bool)
        for d in saida:
            ocupado |= d.mascara
        area_min_px = p.area_min_m2 / (gsd_m * gsd_m)
        for d in novas:  # a mesma regiao nao entra duas vezes
            comum = d.mascara & ocupado
            if comum.sum() > p.solo_sobreposicao_max * d.mascara.sum():
                continue
            if comum.any():  # tira do solo a parte que ja e buraco: area nao conta em dobro
                d.mascara &= ~ocupado
                if d.mascara.sum() < area_min_px:
                    continue
            saida.append(d)
        return saida

    def _passada(
        self,
        sinal: np.ndarray,
        valida: np.ndarray,
        gsd_m: float,
        piso: float,
        classe: str,
        extensa: bool = False,
        porta: np.ndarray | None = None,
    ) -> list[Deteccao]:
        """Regioes onde `sinal` (uint8) fica abaixo do fundo local. `piso` e o contraste minimo.

        `extensa`: mede area em vez de objeto (janela de fundo larga, sem filtro de forma e
        sem o termo k x MAD, porque dano extenso nao e raro na imagem). `porta`: pixels que
        podem entrar na regiao.
        """
        p = self.p
        janela = p.solo_janela_fundo_m if extensa else p.janela_fundo_m
        percentil = p.solo_fundo_percentil if extensa else p.fundo_percentil
        area_max = float("inf") if extensa else p.area_max_m2
        k_mad = p.solo_k_mad if extensa else p.k_mad
        px2 = gsd_m * gsd_m
        if valida.sum() * px2 < p.area_min_m2:
            return []

        # 1. desfoque leve
        sigma = p.suavizacao_m / gsd_m
        if sigma > 0:
            sinal = cv2.GaussianBlur(sinal, (0, 0), sigma)
        sinal_f = sinal.astype(np.float32)

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
        fundo, _ = _fundo(sinal, valida, gsd_m, p, janela, percentil)
        anomalia = fundo - sinal_f
        limiar = _limiar(anomalia[miolo], piso, k_mad)
        bruto = (anomalia > limiar) & miolo
        if not bruto.any():
            return []
        suspeito = cv2.dilate(bruto.astype(np.uint8), _disco(2 * d_morf + 1)) > 0
        fundo2, tem = _fundo(sinal, valida & ~suspeito, gsd_m, p, janela, percentil)
        fundo = np.where(tem, fundo2, fundo)
        anomalia = fundo - sinal_f
        limpo = miolo & ~suspeito
        limiar = _limiar(anomalia[limpo] if limpo.any() else anomalia[miolo], piso, k_mad)

        # 4. limpeza morfologica e componentes conexos
        el = _disco(d_morf)
        binaria = (anomalia > limiar) & miolo
        if porta is not None:
            binaria &= porta
        binaria = binaria.astype(np.uint8)
        binaria = cv2.morphologyEx(binaria, cv2.MORPH_OPEN, el)
        binaria = cv2.morphologyEx(binaria, cv2.MORPH_CLOSE, el)
        binaria[~valida] = 0
        n, rotulos, stats, _ = cv2.connectedComponentsWithStats(binaria, connectivity=8)
        if n <= 1:
            return []

        gx = cv2.Sobel(sinal_f, cv2.CV_32F, 1, 0, ksize=3) / 8.0
        gy = cv2.Sobel(sinal_f, cv2.CV_32F, 0, 1, ksize=3) / 8.0
        grad = cv2.magnitude(gx, gy)

        saida: list[Deteccao] = []
        for i in range(1, n):
            x, y, w, h, area_px = stats[i]
            if area_px * px2 < p.area_min_m2:
                continue
            comp = rotulos[y : y + h, x : x + w] == i
            partes = [comp]
            if not extensa and _forma(comp)[0] < p.solidez_min:
                partes = _separar(comp, p.separacao_nucleo)
            for parte in partes:
                # 5. filtros de forma, em metros
                area_m2 = float(parte.sum()) * px2
                if not (p.area_min_m2 <= area_m2 <= area_max):
                    continue
                solidez, alongamento = _forma(parte)
                if not extensa and (solidez < p.solidez_min or alongamento > p.alongamento_max):
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
                if extensa:  # sem termo de forma: faixa irregular nao e menos real
                    confianca = float(c_contraste + c_borda) / 2.0
                else:
                    confianca = float(c_contraste + c_borda + c_forma) / 3.0
                mascara = np.zeros(valida.shape, dtype=bool)
                mascara[rec] = parte
                saida.append(Deteccao(mascara, round(confianca, 3), classe, extensa))
        return saida
