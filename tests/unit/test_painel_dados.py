"""Formatacao e exportacoes do painel."""

import geopandas as gpd
from shapely.geometry import box

from paviscan.painel import dados


def test_numero_em_formato_brasileiro():
    assert dados.fmt(1234.567) == "1.234,57"
    assert dados.fmt(0.5, 1) == "0,5"
    assert dados.fmt(12, 0) == "12"


def _dados():
    det = gpd.GeoDataFrame(
        {
            "id": [1, 2, 3],
            "area_m2": [0.1, 0.5, 2.0],
            "largura_m": [0.3, 0.6, 1.2],
            "comprimento_m": [0.4, 0.9, 1.8],
            "confianca": [0.2, 0.6, 0.9],
            "severidade": ["baixa", "media", "alta"],
            "segmento": [0, 0, 1],
            "via": ["Rua A", "Rua A", "Rua B"],
        },
        geometry=[box(0, 0, 1, 1), box(5, 0, 6, 1), box(25, 0, 26, 1)],
        crs="EPSG:31980",
    )
    seg = gpd.GeoDataFrame(
        {
            "segmento": [0, 1],
            "via": ["Rua A", "Rua B"],
            "comprimento_m": [20.0, 20.0],
            "area_pista_m2": [140.0, 140.0],
        },
        geometry=[box(0, 0, 20, 7), box(20, 0, 40, 7)],
        crs="EPSG:31980",
    )
    return det, seg


def test_filtro_por_via_e_combinado():
    det, _ = _dados()
    assert list(dados.filtrar(det, vias=["Rua B"])["id"]) == [3]
    assert list(dados.filtrar(det, confianca_min=0.5, severidade=["media", "alta"])["id"]) == [2, 3]
    assert dados.filtrar(det, area_min=0.2, area_max=1.0, segmentos=[0])["id"].tolist() == [2]


def test_resumo_e_ranking_seguem_o_filtro():
    det, seg = _dados()
    r = dados.resumir(det, seg)
    assert r["n_buracos"] == 3 and abs(r["area_total_m2"] - 2.6) < 1e-9
    assert abs(r["pct_afetada"] - 100 * 2.6 / 280) < 1e-9
    assert abs(r["buracos_por_100m"] - 7.5) < 1e-9
    pior = dados.ranking(det, seg)
    assert pior["segmento"].tolist() == [1, 0]
    so_a = dados.ranking(dados.filtrar(det, vias=["Rua A"]), seg)
    assert so_a["segmento"].tolist() == [0]


def test_exportacoes_nao_quebram_vazias():
    det, seg = _dados()
    vazio = dados.filtrar(det, area_min=1e9)
    assert dados.csv_bytes(vazio).startswith(b"\xef\xbb\xbf")
    assert b"FeatureCollection" in dados.geojson_bytes(vazio)
    assert len(dados.xlsx_bytes(vazio, seg)) > 1000
    assert dados.para_mapa(vazio, ["id"])["features"] == []


def test_indicadores_de_uma_via_usam_so_os_segmentos_dela():
    det, seg = _dados()
    so_b = dados.filtrar(det, vias=["Rua B"])
    r = dados.resumir(so_b, dados.filtrar_segmentos(seg, vias=["Rua B"]))
    assert abs(r["pct_afetada"] - 100 * 2.0 / 140) < 1e-9  # 140 m2 da Rua B, nao 280 do total
    assert abs(r["buracos_por_100m"] - 5.0) < 1e-9  # 1 buraco em 20 m
    r0 = dados.resumir(dados.filtrar(det, segmentos=[0]), dados.filtrar_segmentos(seg, ids=[0]))
    assert abs(r0["pct_afetada"] - 100 * 0.6 / 140) < 1e-9
    assert len(dados.filtrar_segmentos(seg)) == 2


def test_contagem_por_severidade_sempre_tem_as_tres_faixas():
    det, _ = _dados()
    assert dados.contagem_severidade(det) == {"baixa": 1, "media": 1, "alta": 1}
    assert dados.contagem_severidade(det.iloc[0:0]) == {"baixa": 0, "media": 0, "alta": 0}


def test_recorte_do_relatorio_escolhe_area_e_defeitos():
    det, seg = _dados()
    det["classe"] = ["panela", "panela", "solo_exposto"]
    det["revisao"] = ["confirmado", "pendente", "manual"]
    d, s = dados.recorte_relatorio(det, seg)
    assert list(d["id"]) == [1, 2, 3] and list(s["segmento"]) == [0, 1]
    d, s = dados.recorte_relatorio(det, seg, seg_de=1, seg_ate=1)
    assert list(d["id"]) == [3] and list(s["segmento"]) == [1]  # area: so o segmento 1
    d, s = dados.recorte_relatorio(det, seg, vias=["Rua A"], severidades=["media"])
    assert list(d["id"]) == [2] and list(s["segmento"]) == [0]  # o segmento conta mesmo sem defeito
    d, _ = dados.recorte_relatorio(det, seg, so_conferidos=True, fora=[3])
    assert list(d["id"]) == [1]
    d, _ = dados.recorte_relatorio(det, seg, classes=["solo_exposto"])
    assert list(d["id"]) == [3]
    d, s = dados.recorte_relatorio(det, seg, vias=[])
    assert d.empty and s.empty
