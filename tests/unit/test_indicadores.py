"""Atribuicao de segmento, agregacao e resumo com numeros conhecidos."""

import geopandas as gpd
import pytest
from shapely.geometry import box

from paviscan import config as config_mod
from paviscan import indicadores, oam, pista


def _cenario():
    via = gpd.GeoDataFrame({"nome": ["Rua A"]}, geometry=[box(0, 0, 40, 7)], crs="EPSG:31980")
    seg = pista.segmentar(via, 20)
    det = gpd.GeoDataFrame(
        {
            "area_m2": [1.0, 0.25, 4.0],
            "largura_m": [1.0, 0.5, 2.0],
            "comprimento_m": [1.0, 0.5, 2.0],
            "confianca": [0.9, 0.5, 0.7],
            "severidade": ["alta", "media", "alta"],
            "classe": ["panela"] * 3,
        },
        geometry=[box(5, 3, 6, 4), box(25, 1, 25.5, 1.5), box(30, 2, 32, 4)],
        crs="EPSG:31980",
    )
    return det, seg


def test_cada_buraco_vai_para_o_segmento_do_centroide():
    det, seg = _cenario()
    d = indicadores.atribuir_segmento(det, seg)
    assert list(d["id"]) == [1, 2, 3]
    assert list(d["segmento"]) == [0, 1, 1]
    assert set(d["via"]) == {"Rua A"}
    assert d["lat"].between(-90, 90).all() and d["lon"].between(-180, 180).all()


def test_agregacao_por_segmento_e_resumo():
    det, seg = _cenario()
    d = indicadores.atribuir_segmento(det, seg)
    s = indicadores.por_segmento(d, seg).set_index("segmento")
    assert list(s["n_buracos"]) == [1, 2]
    assert abs(s.loc[0, "pct_afetada"] - 100 * 1.0 / 140) < 1e-3
    assert abs(s.loc[1, "pct_afetada"] - 100 * 4.25 / 140) < 1e-3
    r = indicadores.resumo(d, s.reset_index(), gsd_m=0.02)
    assert r["n_defeitos"] == 3 and abs(r["area_total_m2"] - 5.25) < 1e-9
    assert abs(r["pct_afetada"] - 100 * 5.25 / 280) < 1e-3
    assert r["comprimento_pista_m"] == 40 and r["n_segmentos"] == 2
    assert r["por_severidade"] == {"baixa": 0, "media": 1, "alta": 2}


def test_sem_buraco_os_indicadores_zeram():
    det, seg = _cenario()
    d = indicadores.atribuir_segmento(det.iloc[0:0], seg)
    assert d.empty and {"id", "segmento", "via"} <= set(d.columns)
    s = indicadores.por_segmento(d, seg)
    assert s["n_buracos"].sum() == 0 and (s["pct_afetada"] == 0).all()
    assert indicadores.resumo(d, s)["pct_afetada"] == 0


def test_id_da_imagem_do_openaerialmap():
    link = "https://imagery.hotosm.org/browse?lat=-3.0&lon=-60.0&zoom=16.4&selected_id=6a7f68e477ca9598cc9fe89b"
    assert oam._id(link) == "6a7f68e477ca9598cc9fe89b"
    assert oam._id("6a7f6f8d77ca9598cc9ff5d2") == "6a7f6f8d77ca9598cc9ff5d2"
    with pytest.raises(ValueError):
        oam._id("https://imagery.hotosm.org/browse?lat=-3.0")


def test_config_padrao_tem_os_blocos_que_o_codigo_le():
    p = config_mod.carregar()
    for bloco in ("tiles", "classico", "severidade", "segmentos", "raster", "pista", "moldura",
                  "previa", "recortes", "pista_osm"):  # fmt: skip
        assert bloco in p, bloco
