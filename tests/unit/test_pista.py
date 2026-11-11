"""Segmentos e janelas: cobertura sem sobra e sem sobreposicao."""

import geopandas as gpd
import numpy as np
from affine import Affine
from shapely import affinity
from shapely.geometry import box

from paviscan import ingestao, pista


def _pista(*geoms):
    return gpd.GeoDataFrame({"nome": [f"via {i}" for i in range(len(geoms))]}, geometry=list(geoms),
                            crs="EPSG:31980")  # fmt: skip


def test_pista_de_60m_vira_3_segmentos_de_20m():
    seg = pista.segmentar(_pista(box(0, 0, 60, 7)), 20)
    assert list(seg["segmento"]) == [0, 1, 2]
    assert np.allclose(seg["comprimento_m"], 20)
    assert np.allclose(seg["area_pista_m2"], 140)
    assert abs(seg.geometry.area.sum() - 420) < 1e-6


def test_pista_inclinada_e_sobra_no_ultimo_segmento():
    pol = affinity.rotate(box(0, 0, 50, 6), 30, origin=(0, 0))
    seg = pista.segmentar(_pista(pol), 20)
    assert len(seg) == 3
    assert sorted(seg["comprimento_m"]) == [10, 20, 20]
    assert abs(seg.geometry.area.sum() - pol.area) < 1e-6
    assert seg.geometry.iloc[0].intersection(seg.geometry.iloc[1]).area < 1e-9


def test_varios_trechos_numeram_em_sequencia():
    seg = pista.segmentar(_pista(box(0, 0, 40, 6), box(100, 0, 106, 30)), 20)
    assert list(seg["segmento"]) == [0, 1, 2, 3]
    assert list(seg["via"]) == ["via 0", "via 0", "via 1", "via 1"]


def test_mascara_da_pista_na_janela():
    tf = Affine(0.1, 0, 0.0, 0, -0.1, 10.0)  # 10 cm/px, origem no canto superior esquerdo
    m = pista.mascara(_pista(box(2, 6, 4, 9)), tf, (100, 100))
    assert m.sum() == 20 * 30
    assert m[10:40, 20:40].all() and not m[0:10].any()


def test_janelas_cobrem_o_raster_com_sobreposicao():
    cobertura = np.zeros((550, 3000), dtype=int)
    inicios = set()
    for j in ingestao.janelas(3000, 550, 1024, 128):
        cobertura[j.row_off : j.row_off + j.height, j.col_off : j.col_off + j.width] += 1
        inicios.add(int(j.col_off))
    assert cobertura.min() >= 1
    # cada emenda entre janelas vizinhas tem os 128 px de sobreposicao
    for c in sorted(inicios)[1:]:
        assert (cobertura[:, c : c + 128] >= 2).all()


def test_zona_utm_de_manaus_e_sirgas_20s():
    assert ingestao.utm_do_ponto(-60.04, -3.0).to_epsg() == 31980  # Taruma, a oeste de 60 W
    assert ingestao.utm_do_ponto(-43.2, -22.9).to_epsg() == 31983  # Rio de Janeiro, 23S
    assert ingestao.utm_do_ponto(2.35, 48.85).to_epsg() == 32631  # Paris, 31N


def _gravar(tmp_path, *geoms):
    arq = tmp_path / "pista.geojson"
    _pista(*geoms).to_file(arq, driver="GeoJSON")
    return arq


def test_trechos_sobrepostos_nao_contam_em_dobro(tmp_path):
    import pytest

    arq = _gravar(tmp_path, box(0, 0, 35, 7), box(25, 0, 60, 7))  # 10 m de sobreposicao
    with pytest.warns(UserWarning, match="sobrepoem"):
        p = pista.carregar(arq, "EPSG:31980")
    assert abs(p.geometry.area.sum() - 60 * 7) < 1e-6
    seg = pista.segmentar(p, 20)
    assert abs(seg["area_pista_m2"].sum() - 420) < 1e-3
    assert abs(seg["comprimento_m"].sum() - 60) < 1e-6


def test_trecho_duplicado_some(tmp_path):
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        p = pista.carregar(_gravar(tmp_path, box(0, 0, 60, 7), box(0, 0, 60, 7)), "EPSG:31980")
    assert len(p) == 1 and abs(p.geometry.area.sum() - 420) < 1e-6


def test_recorte_pela_area_com_dado():
    import pytest

    p = _pista(box(0, 0, 60, 7))
    dentro = pista.recortar(p, box(10, -5, 100, 20))
    assert abs(dentro.geometry.area.sum() - 50 * 7) < 1e-6
    with pytest.raises(ValueError, match="nao cruza"):
        pista.recortar(p, box(500, 500, 600, 600))


def test_sobra_pequena_entra_no_ultimo_segmento():
    seg = pista.segmentar(_pista(box(0, 0, 62, 7)), 20, resto_min_m=5)
    assert list(seg["comprimento_m"]) == [20, 20, 22]
    assert abs(seg["area_pista_m2"].sum() - 62 * 7) < 1e-3
    seg = pista.segmentar(_pista(box(0, 0, 68, 7)), 20, resto_min_m=5)
    assert list(seg["comprimento_m"]) == [20, 20, 20, 8]
