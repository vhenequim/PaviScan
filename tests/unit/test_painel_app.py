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


def _cena_de_terra(pasta: Path, com_buraco: bool = True):
    """Estrada de laterita (vermelha) entre vegetacao, com um buraco escuro no meio."""
    import rasterio
    from rasterio.transform import from_origin

    rng = np.random.default_rng(9)
    h, w, gsd = 400, 1200, 0.05  # 20 m x 60 m
    img = np.empty((3, h, w), np.uint8)
    for k, base in enumerate((70, 120, 55)):  # vegetacao
        img[k] = (base + 6 * rng.standard_normal((h, w))).clip(0, 255)
    for k, base in enumerate((215, 170, 140)):  # estrada de terra de 7 m no meio
        img[k, 130:270] = (base + 5 * rng.standard_normal((140, w))).clip(0, 255)
    if com_buraco:
        yy, xx = np.mgrid[:h, :w]
        img[:, (yy - 200) ** 2 + (xx - 600) ** 2 <= 12**2] = np.array([[95], [70], [55]])
    pasta.mkdir(parents=True, exist_ok=True)
    tf = from_origin(830000.0, 9660000.0, gsd, gsd)
    perfil = dict(driver="GTiff", count=3, dtype="uint8", height=h, width=w, crs="EPSG:31980",
                  transform=tf)  # fmt: skip
    with rasterio.open(pasta / "orto.tif", "w", **perfil) as dst:
        dst.write(img)
    eixo = LineString([(830001.0, 9660000.0 - 10.0), (830059.0, 9660000.0 - 10.0)])
    gpd.GeoDataFrame({"nome": ["Estrada"]}, geometry=[eixo], crs="EPSG:31980").to_file(
        pasta / "eixo.geojson", driver="GeoJSON"
    )
    return pasta


def test_superficie_automatica_reconhece_via_de_terra_e_de_asfalto(tmp_path):
    terra = _cena_de_terra(tmp_path / "terra")
    pista = gpd.read_file(pista_osm.gerar(terra / "orto.tif", terra / "pista.geojson",
                                          eixo=terra / "eixo.geojson"))  # fmt: skip
    assert set(pista["superficie"]) == {"terra"}
    assert 0.8 * 7 * 58 < pista.geometry.area.sum() < 1.25 * 7 * 58  # a estrada de 7 m, nao o mato
    # no modo asfalto a mesma estrada nao e achada: e o que acontecia antes
    with pytest.raises(RuntimeError):
        pista_osm.gerar(terra / "orto.tif", tmp_path / "p.geojson", eixo=terra / "eixo.geojson",
                        superficie="asfalto")  # fmt: skip
    # via de asfalto continua asfalto no modo automatico
    cena = sintetico.gerar_cena(tmp_path / "c", n_buracos=0, gsd=0.05, comprimento_m=40.0, seed=2)
    x0, y0, x1, y1 = gpd.read_file(cena / "pista.geojson").total_bounds
    meio = LineString([(x0 + 1, (y0 + y1) / 2), (x1 - 1, (y0 + y1) / 2)])
    gpd.GeoDataFrame(geometry=[meio], crs="EPSG:31980").to_file(
        tmp_path / "e.geojson", driver="GeoJSON"
    )
    asfalto = gpd.read_file(pista_osm.gerar(cena / "orto.tif", tmp_path / "pa.geojson",
                                            eixo=tmp_path / "e.geojson"))  # fmt: skip
    assert set(asfalto["superficie"]) == {"asfalto"}


def test_buraco_em_via_de_terra_e_detectado_e_nao_ha_revestimento_perdido(tmp_path):
    terra = _cena_de_terra(tmp_path / "terra")
    pista_osm.gerar(terra / "orto.tif", terra / "pista.geojson", eixo=terra / "eixo.geojson")
    saida = pipeline.processar(terra / "orto.tif", terra / "pista.geojson", tmp_path / "run")
    det = gpd.read_file(saida / "defeitos.geojson")
    assert list(det["classe"]) == ["panela"]  # o buraco; nada de "solo exposto" numa via que e solo
    real = np.pi * (12 * 0.05) ** 2
    assert abs(det["area_m2"].iloc[0] - real) / real < 0.25
    limpa = _cena_de_terra(tmp_path / "limpa", com_buraco=False)
    pista_osm.gerar(limpa / "orto.tif", limpa / "pista.geojson", eixo=limpa / "eixo.geojson")
    saida = pipeline.processar(limpa / "orto.tif", limpa / "pista.geojson", tmp_path / "run2")
    assert len(gpd.read_file(saida / "defeitos.geojson")) == 0


def test_editor_de_contorno_monta_nos_dois_modos(dois_runs):
    """O mapa do editor (folium + script de edicao direta) e montado no Python sem erro."""
    at = _app(dois_runs).run()
    at.session_state["aba"] = "Inspeção"
    at.run()
    for modo in ("Corrigir contorno", "Adicionar área que faltou"):
        next(r for r in at.radio if "Triagem rápida" in r.options).set_value(modo).run()
        assert not at.exception, (modo, at.exception)


def test_relatorio_sai_so_com_a_area_e_os_defeitos_escolhidos(dois_runs):
    import cv2
    import numpy as np

    run = dois_runs / "beta"
    det, seg, resumo = dados.carregar(run)
    params = dados.parametros()
    det = revisao.aplicar(det, seg, revisao.carregar(run), params["severidade"])
    assert {"angulo_via", "caixa_ao_longo_m", "caixa_transversal_m"} <= set(det.columns)
    um = int(det.sort_values("area_m2")["id"].iloc[-1])
    d, s = dados.recorte_relatorio(det, seg, fora=[um])
    pagina = relatorio.gerar_html(run, d, s, resumo, params, None, "1 defeito retirado à mão.")
    assert "Recorte deste relatório" in pagina and f"#{um} ·" not in pagina
    jpg = relatorio.mapa_estatico(run, d, resumo, segmentos=s)
    assert cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR) is not None
    # pela tela: o menu aparece, e o relatorio gerado e o do recorte
    at = _app(dois_runs).run()
    at.session_state["aba"] = "Dados e relatório"
    at.run()
    assert not at.exception, at.exception
    assert any("Entram" in c.value for c in at.caption)
    sev = next(m for m in at.multiselect if m.label == "Severidade")
    sev.set_value(["alta"]).run()
    next(b for b in at.button if b.label == "Gerar relatório").click().run()
    assert not at.exception, at.exception
    assinatura, html_rel = at.session_state["relatorio"]
    assert "severidade alta" in html_rel.decode("utf-8")


def test_mapa_do_relatorio_aceita_segmento_com_sobra_de_linha(dois_runs):
    """No Novo Aleixo um segmento e GeometryCollection (poligono + linha) e o relatorio quebrava."""
    import cv2
    import numpy as np
    from shapely.geometry import GeometryCollection, LineString

    run = dois_runs / "alfa"
    det, seg, resumo = dados.carregar(run)
    params = dados.parametros()
    det = revisao.aplicar(det, seg, revisao.carregar(run), params["severidade"])
    seg = seg.copy()
    g = seg.geometry.iloc[0]
    x0, y0, _, _ = g.bounds
    sobra = LineString([(x0, y0), (x0 - 1, y0)])
    seg.loc[seg.index[0], "geometry"] = GeometryCollection([g, sobra])
    jpg = relatorio.mapa_estatico(run, det, resumo, segmentos=seg)
    assert cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR) is not None
    assert "Onde estão os defeitos" in relatorio.gerar_html(run, det, seg, resumo, params, None)


def test_menu_do_relatorio_lembra_a_escolha_ao_trocar_de_secao(dois_runs):
    at = _app(dois_runs).run()
    at.session_state["aba"] = "Dados e relatório"
    at.run()
    next(m for m in at.multiselect if m.label == "Severidade").set_value(["alta"]).run()
    fora = next(m for m in at.multiselect if m.label == "Tirar defeitos específicos")
    um = fora.options[0] if fora.options else None
    if um is not None:
        fora.set_value([fora.options[0]]).run()
    at.session_state["aba"] = "Orçamento"
    at.run()
    at.session_state["aba"] = "Dados e relatório"
    at.run()
    assert not at.exception, at.exception
    assert next(m for m in at.multiselect if m.label == "Severidade").value == ["alta"]
    if um is not None:
        assert (
            len(next(m for m in at.multiselect if m.label == "Tirar defeitos específicos").value)
            == 1
        )


def test_menu_do_relatorio_volta_a_area_inteira_ao_escolher_todas_as_vias(tmp_path):
    """Escolher uma via e depois todas de novo deixava a faixa presa nos segmentos da primeira."""
    import geopandas as gpd

    cena = sintetico.gerar_cena(
        tmp_path / "cena", n_buracos=3, gsd=0.03, comprimento_m=60.0, seed=8
    )
    run = tmp_path / "runs" / "duas_vias"
    pipeline.processar(cena / "orto.tif", cena / "pista.geojson", run)
    for arq in ("segmentos.geojson", "defeitos.geojson"):  # o trecho vira duas vias
        g = gpd.read_file(run / arq)
        g["via"] = ["Rua A" if s == 0 else "Rua B" for s in g["segmento"]]
        g.to_file(run / arq, driver="GeoJSON")
    n_seg = len(gpd.read_file(run / "segmentos.geojson"))
    assert n_seg >= 3
    at = _app(tmp_path / "runs").run()
    at.session_state["aba"] = "Dados e relatório"
    at.run()
    vias = next(m for m in at.multiselect if m.label == "Vias")
    vias.set_value(["Rua B"]).run()
    next(m for m in at.multiselect if m.label == "Vias").set_value(["Rua A", "Rua B"]).run()
    assert not at.exception, at.exception
    faixa = next(s for s in at.select_slider if s.label.startswith("Segmentos"))
    assert tuple(faixa.value) == (0, n_seg - 1)
    assert any(f"em **{n_seg}** segmento" in c.value for c in at.caption)


def test_versao_publicada_abre_so_com_o_pacote_e_sem_editar(dois_runs, tmp_path, monkeypatch):
    """Pacote de scripts/publicar.py: previa em WebP, sem ortofoto; painel so para consulta."""
    import importlib.util

    arq = Path(paviscan.__file__).resolve().parents[2] / "scripts" / "publicar.py"
    spec = importlib.util.spec_from_file_location("publicar", arq)
    publicar = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publicar)
    pacote = tmp_path / "publicado" / "runs"
    publicar._levantamento(dois_runs / "alfa", pacote / "alfa")
    assert (pacote / "alfa" / "previa.webp").is_file()
    assert not (pacote / "alfa" / "previa.png").exists()
    assert "ortofoto_caminho" not in json.loads((pacote / "alfa" / "resumo.json").read_text())
    assert dados.arquivo_previa(pacote / "alfa").suffix == ".webp"
    assert dados.previa_data_url(pacote / "alfa").startswith("data:image/webp;base64,")
    publicar._dependencias(tmp_path)
    assert "rasterio" in (tmp_path / "requirements.txt").read_text()

    monkeypatch.setenv("PAVISCAN_PUBLICADO", "1")
    at = _app(pacote).run()
    assert not at.exception, at.exception
    assert "Nova análise" not in at.get("button_group")[0].options and len(at.metric) >= 5
    at.session_state["aba"] = "Inspeção"
    at.run()
    assert not at.exception, at.exception
    assert not [r for r in at.radio if "Corrigir contorno" in r.options]  # sem editor
    marcas = [b for b in at.button if b.label in ("✓", "✗")]
    assert marcas and all(b.disabled for b in marcas)  # revisao so para consulta
    at.session_state["aba"] = "Dados e relatório"
    at.run()
    next(b for b in at.button if b.label == "Gerar relatório").click().run()
    assert not at.exception, at.exception
    assert "Onde estão os defeitos" in at.session_state["relatorio"][1].decode("utf-8")
