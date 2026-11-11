"""Orcamento conferido com a conta feita a mao, indice de condicao e acervo de imagens."""

import json

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, box

from paviscan import acervo, geometria, orcamento, pipeline, sintetico
from paviscan import config as config_mod
from paviscan.deteccao import criar
from paviscan.painel import dados


@pytest.fixture(scope="module")
def tabelas():
    return orcamento.carregar_tabelas()


@pytest.fixture(scope="module")
def cfg():
    c = dict(config_mod.carregar()["orcamento"])
    c.update(pavimento="CBUQ", terreno="regular", bdi=0.25, margem_corte_m=0.30)
    return c


def _defeitos(linhas):
    """linhas: (id, largura, comprimento, classe, severidade, segmento)."""
    return gpd.GeoDataFrame(
        {
            "id": [x[0] for x in linhas],
            "largura_m": [x[1] for x in linhas],
            "comprimento_m": [x[2] for x in linhas],
            "area_m2": [x[1] * x[2] for x in linhas],
            "classe": [x[3] for x in linhas],
            "severidade": [x[4] for x in linhas],
            "segmento": [x[5] for x in linhas],
            "via": ["Rua A"] * len(linhas),
        },
        geometry=[box(0, 0, 1, 1)] * len(linhas),
        crs="EPSG:31980",
    )


def _segmentos(areas):
    return gpd.GeoDataFrame(
        {"segmento": range(len(areas)), "via": ["Rua A"] * len(areas), "area_pista_m2": areas},
        geometry=[box(0, 0, 20, 7)] * len(areas),
        crs="EPSG:31980",
    )


def test_um_buraco_de_1m2_de_corte_bate_com_a_conta_a_mao(tabelas, cfg):
    solucoes, precos = tabelas
    # 0,40 x 0,40 m com 0,30 m de folga de cada lado: retangulo de corte de 1,00 x 1,00 m
    det = _defeitos([(1, 0.4, 0.4, "panela", "baixa", 0)])
    orc = orcamento.orcar(det, _segmentos([140.0]), solucoes, precos, cfg)
    assert ((orc["quantidade"] * orc["preco_unit"] - orc["custo"]).abs() < 1e-9).all()
    tot = orc.groupby("opcao")["custo"].sum()
    # A: corte 10 + pintura de ligacao 3 + 0,05 m3 de CBUQ a 1500 = 88
    assert abs(tot["A"] - 88.0) < 1e-6
    # B: corte 10 + 0,30 m3 escavacao a 60 + 0,25 m3 brita a 300 + imprimacao 8 + CBUQ 75 = 186
    assert abs(tot["B"] - 186.0) < 1e-6
    assert "C" not in tot.index  # 1 m2 em 140 m2 nao chega a 15 %
    t = orcamento.totais(orc, 0.25)
    assert abs(t["A"]["total"] - 110.0) < 1e-6 and abs(t["B"]["total"] - 232.5) < 1e-6


def test_trecho_muito_afetado_ganha_recapeamento_e_o_plano_troca_os_reparos(tabelas, cfg):
    solucoes, precos = tabelas
    det = _defeitos(
        [
            (1, 0.4, 0.4, "panela", "baixa", 0),  # segmento bom: A
            (2, 1.4, 1.4, "panela", "alta", 0),  # severidade alta: B
            (3, 0.4, 0.4, "solo_exposto", "baixa", 0),  # base exposta: B
            (4, 4.4, 4.4, "panela", "alta", 1),  # corte de 25 m2 em trecho de 140 m2: mais de 15 %
        ]
    )
    orc = orcamento.orcar(det, _segmentos([140.0, 140.0]), solucoes, precos, cfg)
    c = orc[orc["opcao"] == "C"]
    assert set(c["id"]) == {"seg-1"}
    # C: fresagem 140 x 15 + pintura 140 x 3 + 140 x 0,04 m3 de CBUQ a 1500 = 10.920
    assert abs(c["custo"].sum() - 10920.0) < 1e-6
    plano, escolha = orcamento.plano_recomendado(det, orc, cfg)
    assert dict(zip(escolha["id"], escolha["solucao"], strict=True)) == {
        1: "A",
        2: "B",
        3: "B",
        4: "C",
    }
    ids_do_plano = set(plano["id"])
    assert ids_do_plano == {1, 2, 3, "seg-1"}  # o defeito 4 e pago pelo recapeamento do trecho
    assert set(plano.loc[plano["id"] == 1, "opcao"]) == {"A"}
    assert abs(orcamento.total(plano, 0.25)["total"] - 1.25 * plano["custo"].sum()) < 1e-6


def test_classe_extensa_corta_pela_area_e_servico_sem_preco_e_erro(tabelas, cfg):
    solucoes, precos = tabelas
    det = _defeitos([(1, 0.5, 20.0, "solo_exposto", "alta", 0)])  # faixa longa de 10 m2
    corte = orcamento.area_corte(det, cfg)
    assert abs(corte.iloc[0] - 10.0 * cfg["folga_area"]) < 1e-9  # nao o retangulo de 1,1 x 20,6
    with pytest.raises(KeyError, match="sem preco"):
        orcamento.orcar(det, _segmentos([140.0]), solucoes, precos.iloc[1:], cfg)


def test_pavimento_e_terreno_escolhem_as_linhas_certas(tabelas):
    solucoes, _ = tabelas
    b = orcamento.linhas_da_solucao(solucoes, "B", "PMF", "ruim").set_index("servico")
    assert "revestimento_pmf" in b.index and "revestimento_cbuq" not in b.index
    assert b.loc["escavacao_e_remocao", "fator"] == 0.45
    assert b.loc["base_brita_graduada", "fator"] == 0.40


def test_pipeline_grava_orcamento_coerente(tmp_path):
    cena = sintetico.gerar_cena(tmp_path / "c", n_buracos=4, gsd=0.03, comprimento_m=24.0, seed=11)
    saida = pipeline.processar(cena / "orto.tif", cena / "pista.geojson", tmp_path / "run")
    orc = pd.read_csv(saida / "orcamento.csv")
    det = gpd.read_file(saida / "defeitos.geojson")
    assert list(orc.columns) == orcamento.COLUNAS
    assert ((orc["quantidade"] * orc["preco_unit"] - orc["custo"]).abs() < 0.01).all()
    for opcao in ("A", "B"):
        assert set(orc.loc[orc["opcao"] == opcao, "id"].astype(int)) == set(det["id"])
    tot = orc.groupby("opcao")["custo"].sum()
    assert tot["B"] > tot["A"] > 0
    resumo = json.loads((saida / "resumo.json").read_text())
    for opcao in ("A", "B"):
        assert abs(resumo["custo_por_opcao"][opcao] - tot[opcao]) < 0.01
    assert resumo["precos_placeholder"] is True
    assert (saida / "orcamento.xlsx").exists()


def test_indice_de_condicao_e_conceitos():
    cfg = config_mod.carregar()["condicao"]
    assert dados.indice_condicao(0.0, 0.0, cfg) == 100
    nota = dados.indice_condicao(2.0, 10.0, cfg)  # 100 - 8 x 2 - 1 x 10
    assert abs(nota - 74.0) < 1e-9 and dados.conceito(nota, cfg) == "B"
    assert dados.indice_condicao(30.0, 50.0, cfg) == 0 and dados.conceito(0, cfg) == "E"
    assert dados.conceito(85, cfg) == "A" and dados.conceito(84.9, cfg) == "B"


def test_solo_exposto_e_medido_como_area_e_pintura_nao_entra():
    p = config_mod.carregar()
    detector = criar(p["detector"], p[p["detector"]])
    rng = np.random.default_rng(3)
    img = np.empty((400, 600, 3), np.uint8)
    for k, base in enumerate((100, 100, 103)):  # asfalto cinza
        img[..., k] = (base + 3 * rng.standard_normal((400, 600))).clip(0, 255)
    img[190:215, 60:540] = (175, 140, 110)  # faixa de solo: 0,5 m x 9,6 m a 2 cm
    img[300:306, 60:540] = (232, 232, 232)  # pintura branca: clara, mas sem cor
    dets = detector.detectar(img, 0.02, np.ones((400, 600), bool))
    solo = [d for d in dets if d.classe == "solo_exposto"]
    assert len(solo) == 1 and solo[0].extensa
    area = solo[0].mascara.sum() * 0.02**2
    assert abs(area - 0.5 * 9.6) / (0.5 * 9.6) < 0.15
    assert not solo[0].mascara[300:306].any()
    assert not [d for d in dets if d.classe == "panela"]


def test_regiao_extensa_e_unida_entre_janelas_e_repartida_por_segmento():
    a, b = box(0, 0, 12, 1), box(10, 0, 25, 1)  # a mesma faixa vista em duas janelas
    itens = [(a, 0.4, "solo_exposto", float("inf"), False, True),
             (b, 0.6, "solo_exposto", float("inf"), False, True)]  # fmt: skip
    unidos = geometria.deduplicar(itens, 0.2)
    assert len(unidos) == 1 and abs(unidos[0][0].area - 25.0) < 1e-9 and unidos[0][1] == 0.6
    seg = gpd.GeoDataFrame({"segmento": [0, 1]}, geometry=[box(0, -3, 20, 4), box(20, -3, 40, 4)],
                           crs="EPSG:31980")  # fmt: skip
    partes = geometria.repartir(unidos, seg, 0.05)
    assert sorted(round(p[0].area, 6) for p in partes) == [5.0, 20.0]


def test_acervo_guarda_valida_e_lista(tmp_path):
    cena = sintetico.gerar_cena(tmp_path / "c", n_buracos=1, gsd=0.05, comprimento_m=12.0, seed=1)
    pasta = tmp_path / "acervo"
    img = acervo.guardar(cena / "orto.tif", "Trecho 1 / teste", pasta)
    assert img.nome == "Trecho_1_teste" and img.orto.is_file() and img.pista is None
    assert [i.nome for i in acervo.listar(pasta)] == ["Trecho_1_teste"]
    meta = acervo.metadados(img.orto)
    assert abs(meta["gsd_cm"] - 5.0) < 1e-6 and not meta["reprojetada"]
    with pytest.raises(FileExistsError):
        acervo.guardar(cena / "orto.tif", "Trecho 1 / teste", pasta)
    # arquivo que nao e GeoTIFF: erro claro e nada fica para tras
    ruim = tmp_path / "ruim.tif"
    ruim.write_bytes(b"isto nao e uma imagem")
    with pytest.raises(ValueError):
        acervo.guardar(ruim, "ruim", pasta)
    assert not (pasta / "ruim" / "orto.tif").exists()
    assert not list((pasta / "ruim").glob("*.parcial"))
    # vetor de poligonos vira pista; de linhas vira eixo
    assert acervo.guardar_vetor(cena / "pista.geojson", img) == "pista" and img.pista is not None
    linhas = tmp_path / "eixo.geojson"
    gpd.GeoDataFrame(geometry=[LineString([(830000, 9659995), (830012, 9659995)])],
                     crs="EPSG:31980").to_file(linhas, driver="GeoJSON")  # fmt: skip
    assert acervo.guardar_vetor(linhas, img) == "eixo" and img.eixo is not None
    assert img.pista is None  # eixo novo invalida a pista antiga
    acervo.gravar_eixo(
        img, [{"type": "LineString", "coordinates": [[-60.0, -3.0], [-60.0001, -3.0]]}]
    )
    assert json.loads(img.eixo.read_text())["features"][0]["properties"]["nome"] == "Via 1"
    with pytest.raises(ValueError):
        acervo.gravar_eixo(img, [])
