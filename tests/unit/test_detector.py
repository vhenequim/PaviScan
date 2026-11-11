"""Detector classico em imagens controladas."""

import numpy as np
import pytest

from paviscan import config
from paviscan.deteccao import criar


@pytest.fixture(scope="module")
def detector():
    p = config.carregar()
    return criar(p["detector"], p[p["detector"]])


def _asfalto(rng, forma=(400, 400)):
    cinza = (100 + 4 * rng.standard_normal(forma)).clip(0, 255).astype(np.uint8)
    return cinza


def test_disco_escuro_e_detectado_com_a_area_certa(detector):
    rng = np.random.default_rng(0)
    cinza = _asfalto(rng).astype(float)
    yy, xx = np.mgrid[:400, :400]
    disco = (yy - 200) ** 2 + (xx - 180) ** 2 <= 25**2  # raio 25 px = 0,5 m a 2 cm
    cinza[disco] -= 50
    img = np.repeat(cinza.clip(0, 255).astype(np.uint8)[..., None], 3, axis=2)
    dets = detector.detectar(img, 0.02, np.ones((400, 400), bool))
    assert len(dets) == 1
    area = dets[0].mascara.sum() * 0.02**2
    assert abs(area - np.pi * 0.5**2) / (np.pi * 0.5**2) < 0.1
    assert 0.5 < dets[0].confianca <= 1.0


def test_asfalto_limpo_e_linha_fina_nao_geram_deteccao(detector):
    rng = np.random.default_rng(1)
    cinza = _asfalto(rng)
    cinza[100:102, 50:350] = 60  # trinca: escura, 4 cm de largura, 6 m de comprimento
    cinza[300:306, 50:350] = 215  # faixa pintada: clara
    img = np.repeat(cinza[..., None], 3, axis=2)
    assert detector.detectar(img, 0.02, np.ones((400, 400), bool)) == []


def test_fora_da_pista_nao_conta(detector):
    rng = np.random.default_rng(2)
    cinza = _asfalto(rng).astype(float)
    cinza[150:200, 150:200] -= 50
    img = np.repeat(cinza.clip(0, 255).astype(np.uint8)[..., None], 3, axis=2)
    valida = np.ones((400, 400), bool)
    valida[100:250, 100:250] = False
    assert detector.detectar(img, 0.02, valida) == []


def _dois_discos_com_ponte(desl: int) -> np.ndarray:
    """Dois buracos de 0,5 m de raio ligados por uma ponte de 24 cm de largura."""
    rng = np.random.default_rng(4)
    cinza = (100 + 4 * rng.standard_normal((400, 520))).astype(float)
    yy, xx = np.mgrid[:400, :520]
    for cx in (150 + desl, 370 + desl):
        cinza[(yy - 200) ** 2 + (xx - cx) ** 2 <= 25**2] -= 50
    ponte = (abs(yy - 200) <= 6) & (xx > 150 + desl) & (xx < 370 + desl)
    cinza[ponte] = np.minimum(cinza[ponte], 55)
    return np.repeat(cinza.clip(0, 255).astype(np.uint8)[..., None], 3, axis=2)


def test_dois_buracos_colados_viram_dois_e_o_resultado_nao_depende_da_posicao(detector):
    contagens = []
    for desl in (0, 3, 7, 13):
        dets = detector.detectar(_dois_discos_com_ponte(desl), 0.02, np.ones((400, 520), bool))
        contagens.append(len(dets))
    assert contagens == [2, 2, 2, 2]


def test_pixel_colorido_nao_e_asfalto(detector):
    rng = np.random.default_rng(6)
    img = np.repeat(_asfalto(rng)[..., None], 3, axis=2)
    img[150:200, 150:200] = (20, 90, 20)  # mancha verde-escura: vegetacao, nao buraco
    assert detector.detectar(img, 0.02, np.ones((400, 400), bool)) == []
