"""Medidas conferidas contra numeros conhecidos."""

import numpy as np
from affine import Affine
from shapely.geometry import box

from paviscan import geometria
from paviscan.deteccao import Deteccao

FAIXAS = {"media_a_partir_de": 0.25, "alta_a_partir_de": 1.0}


def _quadrado(lado_px: int, y0: int = 10, x0: int = 20) -> np.ndarray:
    m = np.zeros((200, 200), dtype=bool)
    m[y0 : y0 + lado_px, x0 : x0 + lado_px] = True
    return m


def test_quadrado_de_50px_a_2cm_tem_1m2():
    tf = Affine(0.02, 0, 500_000.0, 0, -0.02, 9_000_000.0)
    itens = geometria.vetorizar([Deteccao(_quadrado(50), 0.9)], tf)
    tab = geometria.medir(itens, "EPSG:31980", 0.02, FAIXAS)
    assert len(tab) == 1
    assert abs(tab["area_m2"].iloc[0] - 1.0) < 1e-6
    assert abs(tab["largura_m"].iloc[0] - 1.0) < 1e-3
    assert abs(tab["comprimento_m"].iloc[0] - 1.0) < 1e-3
    assert tab["severidade"].iloc[0] == "alta"
    # posicao: canto superior esquerdo do quadrado em (x0, y0) = (20, 10) px
    assert abs(tab.geometry.iloc[0].bounds[0] - (500_000.0 + 20 * 0.02)) < 1e-6
    assert abs(tab.geometry.iloc[0].bounds[3] - (9_000_000.0 - 10 * 0.02)) < 1e-6


def test_lados_do_retangulo_rotacionado():
    from shapely import affinity

    ret = affinity.rotate(box(0, 0, 3.0, 1.0), 37)
    largura, comprimento = geometria.lados_ret_min(ret)
    assert abs(largura - 1.0) < 1e-6 and abs(comprimento - 3.0) < 1e-6


def test_severidade_pelas_faixas():
    assert geometria.severidade(0.10, FAIXAS) == "baixa"
    assert geometria.severidade(0.25, FAIXAS) == "media"
    assert geometria.severidade(0.99, FAIXAS) == "media"
    assert geometria.severidade(1.00, FAIXAS) == "alta"


def test_do_mesmo_buraco_em_duas_janelas_fica_a_versao_maior():
    inteiro, truncado, outro = box(0, 0, 2, 1), box(1.2, 0, 2, 1), box(5, 5, 6, 6)
    itens = [
        (truncado, 0.8, "panela", 200.0, False, False),  # cortado pela borda da janela: menor
        (inteiro, 0.4, "panela", 10.0, True, False),
        (outro, 0.5, "panela", 50.0, False, False),
    ]
    unicos = geometria.deduplicar(itens, 0.2)
    assert len(unicos) == 2
    # nao ha uniao: sobra exatamente uma das deteccoes, e e a maior, nao a de mais folga
    assert any(g.equals(inteiro) for g, *_ in unicos)
    assert not any(g.equals(truncado) for g, *_ in unicos)


# georreferencia como a da ortofoto do Taruma: pixel e origem nao redondos
TF_REAL = Affine(
    0.029935668143705744, 0, 828405.768963852, 0, -0.02993566814373995, 9667742.037776284
)


def _mascara(forma, l0, l1, c0, c1):
    m = np.zeros(forma, dtype=bool)
    m[l0:l1, c0:c1] = True
    return m


def test_vizinhas_que_so_se_encostam_nao_sao_duplicata_em_coordenadas_reais():
    """Partes separadas pelo watershed dividem uma borda; o arredondamento nao pode apagar uma."""
    perdidas = 0
    rng = np.random.default_rng(0)
    for _ in range(200):
        l0, c0 = (int(v) for v in rng.integers(5, 300, 2))
        h, w1, w2 = (int(v) for v in rng.integers(8, 60, 3))
        a = Deteccao(_mascara((400, 500), l0, l0 + h, c0, c0 + w1), 0.9)
        b = Deteccao(_mascara((400, 500), l0, l0 + h, c0 + w1, c0 + w1 + w2), 0.8)
        itens = geometria.vetorizar([a, b], TF_REAL, [10.0, 20.0], [False, False])
        perdidas += 2 - len(geometria.deduplicar(itens, 0.2))
    assert perdidas == 0


def test_mesma_mascara_vista_de_duas_janelas_e_duplicata_em_coordenadas_reais():
    a = Deteccao(_mascara((400, 500), 100, 140, 300, 350), 0.9)
    # a janela vizinha comeca 250 px a direita: a mesma regiao, em outras coordenadas de pixel
    b = Deteccao(_mascara((400, 500), 100, 140, 50, 100), 0.7)
    itens = geometria.vetorizar([a], TF_REAL, [150.0], [False])
    itens += geometria.vetorizar([b], TF_REAL @ Affine.translation(250, 0), [50.0], [False])
    assert len(geometria.deduplicar(itens, 0.2)) == 1


def test_sem_deteccao_devolve_tabela_vazia_com_colunas():
    tab = geometria.medir([], "EPSG:31980", 0.02, FAIXAS)
    assert tab.empty and "area_m2" in tab.columns and tab.crs is not None


def _disco(raio_px: int) -> np.ndarray:
    yy, xx = np.mgrid[:200, :200]
    return (yy - 100) ** 2 + (xx - 100) ** 2 <= raio_px**2


def test_contorno_leve_tem_menos_vertices_e_a_mesma_area():
    tf = Affine(0.02, 0, 500_000.0, 0, -0.02, 9_000_000.0)
    # disco de 1,6 m de diametro, com a borda em escada dos pixels
    bruto = geometria.vetorizar([Deteccao(_disco(80), 0.9)], tf)[0][0]
    cfg = {"tolerancia_min_m": 0.03, "tolerancia_rel": 0.03, "variacao_area_max": 0.2}
    leve = geometria.contorno_leve(bruto, 0.02, cfg)
    so_pixel = geometria.contorno_leve(bruto, 0.02)  # sem cfg: so o meio pixel de antes
    n = lambda g: len(g.exterior.coords)  # noqa: E731
    assert n(leve) < n(so_pixel) / 2 and n(leve) >= 8
    assert abs(leve.area - bruto.area) < 1e-6  # area dos pixels mantida
    assert leve.symmetric_difference(bruto).area / bruto.area < 0.05  # mesma forma
    item = (bruto, 0.9, "panela", 5.0, False, False)
    tab = geometria.medir([item], "EPSG:31980", 0.02, FAIXAS, cfg)
    assert abs(tab["area_m2"].iloc[0] - bruto.area) < 1e-3


def test_caixa_e_o_menor_retangulo_paralelo_a_via():
    from shapely import affinity

    buraco = affinity.rotate(box(0, 0, 2.0, 0.5), 30, origin=(0, 0))  # retangulo 2 x 0,5 a 30 graus
    rets, ao_longo, transversal = geometria.caixas([buraco], [30.0])
    assert abs(ao_longo[0] - 2.0) < 1e-9 and abs(transversal[0] - 0.5) < 1e-9
    assert (
        rets[0].symmetric_difference(buraco).area < 1e-9
    )  # na direcao certa, e o proprio retangulo
    # via a 0 grau: a caixa e o retangulo alinhado aos eixos que envolve o buraco inclinado
    rets0, l0, t0 = geometria.caixas([buraco], [0.0])
    x0, y0, x1, y1 = buraco.bounds
    assert abs(l0[0] - (x1 - x0)) < 1e-9 and abs(t0[0] - (y1 - y0)) < 1e-9
    assert rets0[0].contains(buraco.buffer(-1e-9))


def test_com_caixa_usa_a_direcao_do_segmento():
    import geopandas as gpd

    det = gpd.GeoDataFrame(
        {"id": [1, 2], "segmento": [0, 1]},
        geometry=[box(0, 0, 1.0, 0.4), box(10, 0, 11.0, 0.4)],
        crs="EPSG:31980",
    )
    d = geometria.com_caixa(det, {0: 0.0, 1: 90.0})
    assert list(d["angulo_via"]) == [0.0, 90.0]
    assert np.allclose(d["caixa_ao_longo_m"], [1.0, 0.4])  # via vertical: ao longo e o lado y
    assert np.allclose(d["caixa_transversal_m"], [0.4, 1.0])
    vazio = geometria.com_caixa(det.iloc[0:0], {})
    assert {"angulo_via", "caixa_ao_longo_m", "caixa_transversal_m"} <= set(vazio.columns)


def test_contorno_leve_em_multipoligono_mantem_cada_parte_no_lugar():
    from shapely.geometry import MultiPolygon, Point

    cfg = {"tolerancia_min_m": 0.03, "tolerancia_rel": 0.03, "variacao_area_max": 0.2}
    a, b = Point(0, 0).buffer(0.5, 64), Point(5, 0).buffer(0.3, 64)
    leve = geometria.contorno_leve(MultiPolygon([a, b]), 0.02, cfg)
    partes = sorted(leve.geoms, key=lambda p: p.centroid.x)
    for antes, depois in zip((a, b), partes, strict=True):
        assert abs(depois.area - antes.area) < 1e-6  # cada parte com a propria area
        assert depois.centroid.distance(antes.centroid) < 1e-3  # e no proprio lugar
