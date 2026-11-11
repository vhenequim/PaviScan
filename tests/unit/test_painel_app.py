"""Fluxos do painel e pontos que a revisao independente achou quebrados."""

import json
import os
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString

import paviscan
from paviscan import acervo, orcamento, pipeline, pista_osm, relatorio, revisao, sintetico
from paviscan import config as config_mod
from paviscan.deteccao import criar
from paviscan.painel import dados


@pytest.fixture(scope="module")
def dois_runs(tmp_path_factory):
    """Pasta de levantamentos com duas cenas pequenas processadas."""
    base = tmp_path_factory.mktemp("painel")
    for nome, semente in (("alfa", 11), ("beta", 5)):
        cena = sintetico.gerar_cena(
            base / "cenas" / nome, n_buracos=4, gsd=0.03, comprimento_m=24.0, seed=semente
        )
        pipeline.processar(cena / "orto.tif", cena / "pista.geojson", base / "runs" / nome)
    return base / "runs"


def _app(runs: Path):
    from streamlit.testing.v1 import AppTest

    os.environ["PAVISCAN_RUNS"] = str(runs)
    arq = Path(paviscan.__file__).parent / "painel" / "app.py"
    return AppTest.from_file(str(arq), default_timeout=180)


def _metrica(at, rotulo: str) -> str:
    return next(m.value for m in at.metric if m.label == rotulo)


def test_levantamento_recem_processado_abre_sem_erro(dois_runs):
    """Com 2 ou mais levantamentos, escolher o novo depois de processar dava excecao."""
    at = _app(dois_runs).run()
    assert not at.exception and at.session_state["levantamento"] == "alfa"
    at.session_state["levantamento_pendente"] = "beta"
    at.run()
    assert not at.exception, at.exception
    assert at.session_state["levantamento"] == "beta"


def test_cada_secao_abre_e_a_escolhida_sobrevive_a_um_clique(dois_runs):
    at = _app(dois_runs).run()
    for secao in ("Vias e trechos", "Inspeção", "Orçamento", "Dados e relatório", "Nova análise",
                  "Próximos passos"):  # fmt: skip
        at.session_state["aba"] = secao
        at.run()
        assert not at.exception, (secao, at.exception)
        assert len(at.metric) >= 5
    assert at.session_state["aba"] == "Próximos passos"


def test_marcar_falso_positivo_na_triagem_tira_dos_indicadores(dois_runs):
    run = dois_runs / "alfa"
    revisao.caminho(run).unlink(missing_ok=True)
    at = _app(dois_runs).run()
    antes = int(_metrica(at, "Defeitos"))
    at.session_state["aba"] = "Inspeção"
    at.run()
    assert not at.exception, at.exception
    botao = next(b for b in at.button if b.label == "✗")
    botao.click().run()
    assert not at.exception, at.exception
    assert at.session_state["aba"] == "Inspeção"  # o clique nao tira o usuario da secao
    rev = revisao.carregar(run)
    assert [v["situacao"] for v in rev["defeitos"].values()] == ["falso_positivo"]
    assert int(_metrica(at, "Defeitos")) == antes - 1
    # confirmar outro nao muda a contagem; desfazer devolve o primeiro
    next(b for b in at.button if b.label == "✓").click().run()
    assert int(_metrica(at, "Defeitos")) == antes - 1
    revisao.caminho(run).unlink()


def test_parametros_do_orcamento_valem_com_a_secao_fechada(dois_runs):
    at = _app(dois_runs).run()
    padrao = _metrica(at, "Reparo estimado")
    at.session_state["aba"] = "Orçamento"
    at.run()
    next(n for n in at.number_input if n.label == "BDI (%)").set_value(50.0).run()
    assert not at.exception, at.exception
    com_bdi = _metrica(at, "Reparo estimado")
    assert com_bdi != padrao
    at.session_state["aba"] = "Visão geral"
    at.run()
    assert _metrica(at, "Reparo estimado") == com_bdi  # nao volta ao padrao ao sair da secao


def test_relatorio_gera_e_nao_imprime_none(dois_runs):
    run = dois_runs / "beta"
    det, seg, resumo = dados.carregar(run)
    params = dados.parametros()
    det = revisao.aplicar(det, seg, revisao.carregar(run), params["severidade"])
    sol, precos = orcamento.carregar_tabelas()
    cfg = params["orcamento"]
    plano, _ = orcamento.plano_recomendado(det, orcamento.orcar(det, seg, sol, precos, cfg), cfg)
    pacote = {"total": orcamento.total(plano, cfg["bdi"]), "bdi": cfg["bdi"],
              "por_servico": orcamento.por_servico(plano), "placeholder": True}  # fmt: skip
    resumo["fonte"] = {"titulo": "Trecho <b>x</b>", "produtor": None, "licenca": None,
                       "origem": "OpenAerialMap", "data": ""}  # fmt: skip
    pagina = relatorio.gerar_html(run, det, seg, resumo, params, pacote)
    assert "None" not in pagina and "Trecho &lt;b&gt;x&lt;/b&gt;" in pagina
    assert "Estimativa de reparo" in pagina and "ilustrativos" in pagina
    vazio = relatorio.gerar_html(run, det.iloc[0:0], seg, resumo, params, None)
    assert "Condição geral" in vazio


def test_pista_a_partir_de_eixo_desenhado(tmp_path):
    cena = sintetico.gerar_cena(tmp_path / "c", n_buracos=0, gsd=0.05, comprimento_m=40.0, seed=2)
    real = gpd.read_file(cena / "pista.geojson").geometry.iloc[0]
    x0, y0, x1, y1 = real.bounds
    eixo = tmp_path / "eixo.geojson"
    meio = LineString([(x0 + 1, (y0 + y1) / 2), (x1 - 1, (y0 + y1) / 2)])
    gpd.GeoDataFrame({"nome": ["Via teste"]}, geometry=[meio], crs="EPSG:31980").to_file(
        eixo, driver="GeoJSON"
    )
    saida = pista_osm.gerar(cena / "orto.tif", tmp_path / "pista.geojson", eixo=eixo)
    feita = gpd.read_file(saida)
    assert set(feita["nome"]) == {"Via teste"} and set(feita["fonte"]) == {"eixo desenhado"}
    uniao = feita.geometry.union_all()
    assert uniao.intersection(real).area / uniao.union(real).area > 0.85
    with pytest.raises(ValueError, match="superficie"):
        pista_osm.gerar(cena / "orto.tif", tmp_path / "p2.geojson", eixo=eixo, superficie="gelo")
    with pytest.raises(FileNotFoundError):
        pista_osm.gerar(cena / "orto.tif", tmp_path / "p3.geojson", eixo=tmp_path / "nao.geojson")
    with pytest.raises(ValueError, match="linha"):
        pista_osm.gerar(cena / "orto.tif", tmp_path / "p4.geojson", eixo=cena / "pista.geojson")


def test_regiao_de_solo_maior_que_80m2_numa_janela_nao_some():
    p = config_mod.carregar()
    detector = criar(p["detector"], p[p["detector"]])
    rng = np.random.default_rng(8)
    img = np.empty((600, 1000, 3), np.uint8)
    for k, base in enumerate((100, 100, 103)):
        img[..., k] = (base + 3 * rng.standard_normal((600, 1000))).clip(0, 255)
    img[260:340, 60:940] = (175, 140, 110)  # 4 m x 44 m a 5 cm = 176 m2, com asfalto dos dois lados
    solo = [d for d in detector.detectar(img, 0.05, np.ones((600, 1000), bool)) if d.extensa]
    area = sum(d.mascara.sum() for d in solo) * 0.05**2
    assert abs(area - 176.0) / 176.0 < 0.15


def test_pedacos_de_regiao_extensa_nao_contam_na_densidade():
    cfg = config_mod.carregar()["condicao"]
    from shapely.geometry import box

    seg = gpd.GeoDataFrame(
        {"segmento": [0], "via": ["Rua A"], "comprimento_m": [20.0], "area_pista_m2": [140.0]},
        geometry=[box(0, 0, 20, 7)], crs="EPSG:31980",
    )  # fmt: skip
    det = gpd.GeoDataFrame(
        {"id": [1, 2, 3, 4], "area_m2": [0.5, 0.5, 0.5, 0.5], "segmento": [0, 0, 0, 0],
         "classe": ["panela", "solo_exposto", "solo_exposto", "solo_exposto"]},
        geometry=[box(1, 1, 2, 2)] * 4, crs="EPSG:31980",
    )  # fmt: skip
    s = dados.condicao_segmentos(det, seg, cfg)
    assert s["n_buracos"].iloc[0] == 4 and s["n_objetos"].iloc[0] == 1
    # area: 2,0 de 140 m2 = 1,43 % -> 11,4 pontos; densidade: 1 buraco em 20 m = 5 por 100 m -> 5
    assert abs(s["indice"].iloc[0] - (100 - 8 * 100 * 2.0 / 140 - 5.0)) < 1e-6
    assert abs(dados.condicao_geral(det, seg, cfg)["indice"] - s["indice"].iloc[0]) < 1e-6


def test_substituir_imagem_apaga_o_credito_antigo_e_envio_ruim_nao_deixa_pasta(tmp_path):
    cena = sintetico.gerar_cena(tmp_path / "c", n_buracos=0, gsd=0.05, comprimento_m=12.0, seed=1)
    pasta = tmp_path / "acervo"
    img = acervo.guardar(cena / "orto.tif", "area", pasta)
    (img.pasta / "stac_item.json").write_text(json.dumps({"properties": {"title": "antiga"}}))
    acervo.guardar(cena / "orto.tif", "area", pasta, substituir=True)
    assert not (img.pasta / "stac_item.json").exists()
    ruim = tmp_path / "ruim.tif"
    ruim.write_bytes(b"nao e imagem")
    with pytest.raises(ValueError):
        acervo.guardar(ruim, "quebrada", pasta)
    assert not (pasta / "quebrada").exists()


def test_processar_pela_tela_abre_o_levantamento_novo_sem_erro(dois_runs, tmp_path, monkeypatch):
    """Clica em Processar de verdade: o fim do processamento troca de levantamento e de secao.

    Duas vezes essa troca terminou em StreamlitWidgetAlreadyInstantiatedError, porque mexia
    na chave de um controle ja desenhado. So um teste que aperta o botao pega isso.
    """
    cena = sintetico.gerar_cena(tmp_path / "c", n_buracos=3, gsd=0.03, comprimento_m=24.0, seed=4)
    pasta = tmp_path / "acervo"
    img = acervo.guardar(cena / "orto.tif", "nova", pasta)
    acervo.guardar_vetor(cena / "pista.geojson", img)
    monkeypatch.setenv("PAVISCAN_IMAGENS", str(pasta))
    at = _app(dois_runs).run()
    at.session_state["aba"] = "Nova análise"
    at.run()
    assert not at.exception, at.exception
    next(b for b in at.button if b.label == "Processar").click().run()
    assert not at.exception, at.exception
    assert (dois_runs / "nova" / "resumo.json").is_file()
    assert at.session_state["levantamento"] == "nova"
    assert at.session_state["aba"] == "Visão geral"
    assert int(_metrica(at, "Defeitos")) >= 3
    # a tela seguinte continua utilizavel
    at.session_state["aba"] = "Inspeção"
    at.run()
    assert not at.exception, at.exception
