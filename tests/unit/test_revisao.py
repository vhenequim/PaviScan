"""Revisao manual: marcar, corrigir o contorno, adicionar area e o efeito nos numeros."""

import json

import geopandas as gpd
import pytest
from shapely.geometry import box, mapping

from paviscan import revisao
from paviscan.painel import dados

FAIXAS = {"media_a_partir_de": 0.25, "alta_a_partir_de": 1.0}
CRS = "EPSG:31980"
E0, N0 = 830000.0, 9660000.0  # origem da cena sintetica


def _cenario():
    seg = gpd.GeoDataFrame(
        {
            "segmento": [0, 1],
            "via": ["Rua A", "Rua B"],
            "comprimento_m": [20.0, 20.0],
            "area_pista_m2": [140.0, 140.0],
        },
        geometry=[box(E0, N0, E0 + 20, N0 + 7), box(E0 + 20, N0, E0 + 40, N0 + 7)],
        crs=CRS,
    )
    geoms = [box(E0 + 2, N0 + 2, E0 + 3, N0 + 3), box(E0 + 25, N0 + 2, E0 + 25.5, N0 + 2.5)]
    det = gpd.GeoDataFrame(
        {
            "id": [1, 2],
            "area_m2": [1.0, 0.25],
            "largura_m": [1.0, 0.5],
            "comprimento_m": [1.0, 0.5],
            "confianca": [0.9, 0.4],
            "severidade": ["alta", "media"],
            "classe": ["panela", "panela"],
            "segmento": [0, 1],
            "via": ["Rua A", "Rua B"],
            "na_emenda": [False, False],
            "x": [E0 + 2.5, E0 + 25.25],
            "y": [N0 + 2.5, N0 + 2.25],
            "lon": [0.0, 0.0],
            "lat": [0.0, 0.0],
        },
        geometry=geoms,
        crs=CRS,
    )
    return det, seg


def _desenho(geom) -> dict:
    """Como o editor devolve: GeoJSON no SRC do levantamento."""
    return mapping(geom)


def test_sem_revisao_tudo_fica_pendente_e_nada_muda(tmp_path):
    det, seg = _cenario()
    r = revisao.aplicar(det, seg, revisao.carregar(tmp_path), FAIXAS)
    assert list(r["revisao"]) == ["pendente", "pendente"] and not r["corrigido"].any()
    assert list(r["area_m2"]) == [1.0, 0.25] and len(r) == 2
    assert revisao.contagem(r) == {
        "pendente": 2, "confirmado": 0, "falso_positivo": 0, "manual": 0,
        "corrigidos": 0, "revisados": 0, "do_detector": 2,
    }  # fmt: skip


def test_marcar_grava_e_o_falso_positivo_sai_dos_indicadores(tmp_path):
    det, seg = _cenario()
    revisao.marcar(tmp_path, 1, "confirmado")
    revisao.marcar(tmp_path, 2, "falso_positivo")
    r = revisao.aplicar(det, seg, revisao.carregar(tmp_path), FAIXAS)
    assert dict(zip(r["id"], r["revisao"], strict=True)) == {1: "confirmado", 2: "falso_positivo"}
    # o detector nao e reescrito: o falso positivo continua na tabela, quem tira e o filtro
    validos = dados.filtrar(r, situacoes=["pendente", "confirmado", "manual"])
    assert list(validos["id"]) == [1]
    assert abs(dados.resumir(validos, seg)["area_total_m2"] - 1.0) < 1e-9
    assert revisao.contagem(r)["revisados"] == 2
    # voltar a pendente apaga a marca
    revisao.marcar(tmp_path, 2, "pendente")
    assert "2" not in revisao.carregar(tmp_path)["defeitos"]
    with pytest.raises(ValueError):
        revisao.marcar(tmp_path, 1, "talvez")


def test_corrigir_troca_o_contorno_e_recalcula_as_medidas(tmp_path):
    det, seg = _cenario()
    novo = box(E0 + 22, N0 + 1, E0 + 24, N0 + 2)  # 2,0 x 1,0 m, agora no segmento da Rua B
    revisao.corrigir(tmp_path, 1, [_desenho(novo)])
    r = revisao.aplicar(det, seg, revisao.carregar(tmp_path), FAIXAS).set_index("id")
    assert r.loc[1, "revisao"] == "confirmado" and bool(r.loc[1, "corrigido"])
    assert abs(r.loc[1, "area_m2"] - 2.0) < 0.01
    assert abs(r.loc[1, "largura_m"] - 1.0) < 0.01 and abs(r.loc[1, "comprimento_m"] - 2.0) < 0.01
    assert r.loc[1, "severidade"] == "alta"
    assert r.loc[1, "segmento"] == 1 and r.loc[1, "via"] == "Rua B"
    assert abs(r.loc[1, "x"] - (E0 + 23)) < 0.01 and -4 < r.loc[1, "lat"] < -2
    assert abs(r.loc[2, "area_m2"] - 0.25) < 1e-9  # o outro defeito nao mexe
    # desfazer volta ao contorno do detector
    revisao.desfazer(tmp_path, 1)
    r = revisao.aplicar(det, seg, revisao.carregar(tmp_path), FAIXAS).set_index("id")
    assert r.loc[1, "revisao"] == "pendente" and abs(r.loc[1, "area_m2"] - 1.0) < 1e-9


def test_adicionar_area_manual_entra_na_tabela_e_nos_numeros(tmp_path):
    det, seg = _cenario()
    a = box(E0 + 10, N0 + 3, E0 + 10.6, N0 + 3.5)  # 0,30 m2 no segmento 0
    b = box(E0 + 30, N0 + 4, E0 + 32, N0 + 5)  # 2,00 m2 no segmento 1
    criados = revisao.adicionar(tmp_path, [_desenho(a), _desenho(b)], "solo_exposto", proximo_id=3)
    assert criados == [3, 4]
    r = revisao.aplicar(det, seg, revisao.carregar(tmp_path), FAIXAS)
    manuais = r[r["revisao"] == "manual"].set_index("id")
    assert list(manuais.index) == [3, 4] and set(manuais["classe"]) == {"solo_exposto"}
    assert abs(manuais.loc[3, "area_m2"] - 0.30) < 0.01 and manuais.loc[3, "severidade"] == "media"
    assert manuais.loc[3, "segmento"] == 0 and manuais.loc[4, "segmento"] == 1
    assert (manuais["confianca"] == 1.0).all()
    assert abs(dados.resumir(r, seg)["area_total_m2"] - (1.0 + 0.25 + 0.30 + 2.0)) < 0.02
    assert r.crs is not None and r.crs.is_projected
    # o id manual nunca e reaproveitado; remover tira so aquela area
    revisao.remover(tmp_path, 3)
    assert revisao.adicionar(tmp_path, [_desenho(a)], "panela", proximo_id=3) == [5]
    # corrigir uma area manual troca a geometria dela, sem virar defeito do detector
    revisao.corrigir(tmp_path, 4, [_desenho(box(E0 + 30, N0 + 4, E0 + 31, N0 + 5))])
    rev = revisao.carregar(tmp_path)
    assert "4" not in rev["defeitos"]
    r = revisao.aplicar(det, seg, rev, FAIXAS).set_index("id")
    assert abs(r.loc[4, "area_m2"] - 1.0) < 0.01 and r.loc[4, "revisao"] == "manual"


def test_desenho_invalido_e_erro_e_revisao_antiga_e_ignorada(tmp_path):
    det, seg = _cenario()
    with pytest.raises(ValueError, match="poligono"):
        revisao.corrigir(tmp_path, 1, [])
    with pytest.raises(ValueError, match="poligono"):
        revisao.adicionar(
            tmp_path, [{"type": "LineString", "coordinates": [[0, 0], [1, 1]]}], "panela", 3
        )
    assert not revisao.caminho(tmp_path).exists()
    # marca de um defeito que nao existe mais (imagem reprocessada) nao quebra nada
    revisao.caminho(tmp_path).write_text(
        json.dumps({"defeitos": {"99": {"situacao": "confirmado"}}})
    )
    r = revisao.aplicar(det, seg, revisao.carregar(tmp_path), FAIXAS)
    assert len(r) == 2 and set(r["revisao"]) == {"pendente"}


def test_reancorar_leva_a_revisao_pelo_contorno_e_nao_pelo_numero():
    a, b, c = box(0, 0, 1, 1), box(10, 0, 11, 1), box(20, 0, 21, 1)
    antigos = gpd.GeoDataFrame({"id": [1, 2, 3]}, geometry=[a, b, c], crs=CRS)
    # reprocessado: b virou 1, a virou 2 (um pouco menor), c sumiu, d e novo
    novos = gpd.GeoDataFrame(
        {"id": [1, 2, 3, 4]},
        geometry=[b, box(0, 0, 1, 0.9), box(30, 0, 31, 1), box(40, 0, 41, 1)],
        crs=CRS,
    )
    contorno = mapping(box(0.2, 0.2, 0.8, 0.8))
    rev = {
        "defeitos": {
            "1": {
                "situacao": "falso_positivo",
                "geometria": contorno,
                "quando": "2026-11-11T10:00:00",
            },
            "2": {"situacao": "confirmado", "quando": "2026-11-11T10:00:01"},
            "3": {"situacao": "confirmado", "quando": "2026-11-11T10:00:02"},
        },
        "adicionados": [{"id": 4, "classe": "panela", "geometria": contorno, "quando": "x"}],
    }
    nova, perdidos = revisao.reancorar(rev, antigos, novos, 0.5)
    assert nova["defeitos"]["2"]["situacao"] == "falso_positivo"  # o antigo 1 (a) agora e o 2
    assert nova["defeitos"]["2"]["geometria"] == contorno
    assert nova["defeitos"]["1"]["situacao"] == "confirmado"  # o antigo 2 (b) agora e o 1
    assert perdidos == ["3"] and nova["sem_par"][0]["id_antigo"] == 3  # c sumiu: nao cai em outro
    assert nova["adicionados"][0]["id"] == 5  # area manual nao colide com o id novo 4


def test_reancorar_junta_duas_revisoes_do_mesmo_defeito():
    antigos = gpd.GeoDataFrame(
        {"id": [1, 2]}, geometry=[box(0, 0, 1, 1), box(0, 0, 1, 1.05)], crs=CRS
    )
    novos = gpd.GeoDataFrame({"id": [7]}, geometry=[box(0, 0, 1, 1)], crs=CRS)
    contorno = mapping(box(0.2, 0.2, 0.8, 0.8))
    rev = {
        "defeitos": {
            "1": {"situacao": "confirmado", "geometria": contorno, "quando": "2026-11-11T10:00:00"},
            "2": {"situacao": "falso_positivo", "quando": "2026-11-12T10:00:00"},
        },
        "adicionados": [],
    }
    nova, perdidos = revisao.reancorar(rev, antigos, novos, 0.5)
    assert not perdidos
    assert nova["defeitos"]["7"]["situacao"] == "falso_positivo"  # vale a mais recente
    assert nova["defeitos"]["7"]["geometria"] == contorno  # e o contorno corrigido fica


def test_reprocessar_mantem_a_revisao_no_mesmo_defeito(tmp_path):
    import warnings

    from paviscan import pipeline, sintetico

    cena = sintetico.gerar_cena(
        tmp_path / "cena", n_buracos=4, gsd=0.03, comprimento_m=24.0, seed=3
    )
    run = tmp_path / "run"
    pipeline.processar(cena / "orto.tif", cena / "pista.geojson", run)
    det = gpd.read_file(run / "defeitos.geojson")
    alvo = det.sort_values("area_m2").iloc[-1]
    # simula a numeracao de um processamento anterior: ids ao contrario
    n = len(det)
    det["id"] = n + 1 - det["id"]
    det.to_file(run / "defeitos.geojson", driver="GeoJSON")
    id_antigo = n + 1 - int(alvo["id"])
    marca = {"situacao": "falso_positivo", "quando": "x"}
    revisao.gravar(run, {"defeitos": {str(id_antigo): marca},
                         "adicionados": []})  # fmt: skip
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pipeline.processar(cena / "orto.tif", cena / "pista.geojson", run)
    rev = revisao.carregar(run)
    assert list(rev["defeitos"]) == [str(int(alvo["id"]))]  # voltou para o defeito certo
    # imagem trocada: a revisao antiga e guardada e a nova comeca vazia
    resumo = json.loads((run / "resumo.json").read_text())
    resumo["ortofoto_caminho"] = "/outra/imagem.tif"
    (run / "resumo.json").write_text(json.dumps(resumo))
    with pytest.warns(UserWarning, match="ortofoto diferente"):
        pipeline.processar(cena / "orto.tif", cena / "pista.geojson", run)
    assert not revisao.caminho(run).exists() and len(list(run.glob("revisao_*.json"))) == 1


def test_reprocessar_sem_deteccao_com_area_manual(tmp_path):
    """O defeitos.geojson vazio volta sem a coluna id; reprocessar dava KeyError."""
    import warnings

    from paviscan import pipeline, sintetico

    cena = sintetico.gerar_cena(
        tmp_path / "cena", n_buracos=0, gsd=0.03, comprimento_m=24.0, seed=4
    )
    run = tmp_path / "run"
    pipeline.processar(cena / "orto.tif", cena / "pista.geojson", run)
    assert len(gpd.read_file(run / "defeitos.geojson")) == 0
    seg = gpd.read_file(run / "segmentos.geojson")
    c = seg.geometry.iloc[0].centroid
    revisao.adicionar(run, [mapping(box(c.x, c.y, c.x + 0.5, c.y + 0.5))], "panela", 1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pipeline.processar(cena / "orto.tif", cena / "pista.geojson", run)
    rev = revisao.carregar(run)
    assert [a["id"] for a in rev["adicionados"]] == [1] and not rev["defeitos"]


def test_falha_no_meio_do_reprocessamento_nao_desalinha_a_revisao(tmp_path, monkeypatch):
    """A revisao nova so vai para o disco depois do defeitos.geojson novo."""
    import warnings

    from paviscan import orcamento, pipeline, sintetico

    cena = sintetico.gerar_cena(
        tmp_path / "cena", n_buracos=4, gsd=0.03, comprimento_m=24.0, seed=3
    )
    run = tmp_path / "run"
    pipeline.processar(cena / "orto.tif", cena / "pista.geojson", run)
    det = gpd.read_file(run / "defeitos.geojson")
    n = len(det)
    det["id"] = n + 1 - det["id"]  # numeracao de um processamento anterior
    det.to_file(run / "defeitos.geojson", driver="GeoJSON")
    revisao.gravar(run, {"defeitos": {"1": {"situacao": "falso_positivo", "quando": "x"}},
                         "adicionados": []})  # fmt: skip
    antes = revisao.caminho(run).read_text()

    def quebra(*a, **k):
        raise RuntimeError("falha simulada no orcamento")

    monkeypatch.setattr(orcamento, "orcar", quebra)
    with warnings.catch_warnings(), pytest.raises(RuntimeError, match="simulada"):
        warnings.simplefilter("ignore")
        pipeline.processar(cena / "orto.tif", cena / "pista.geojson", run)
    assert revisao.caminho(run).read_text() == antes  # continua casando com o geojson antigo
    assert list(gpd.read_file(run / "defeitos.geojson")["id"]) == list(det["id"])


def test_falha_ao_gravar_a_planilha_nao_desalinha_a_revisao(tmp_path, monkeypatch):
    """Planilha aberta no Excel trava a gravacao; o defeitos.geojson novo nao pode ir antes."""
    import warnings

    from paviscan import exportar, pipeline, sintetico

    cena = sintetico.gerar_cena(
        tmp_path / "cena", n_buracos=4, gsd=0.03, comprimento_m=24.0, seed=3
    )
    run = tmp_path / "run"
    pipeline.processar(cena / "orto.tif", cena / "pista.geojson", run)
    det = gpd.read_file(run / "defeitos.geojson")
    det["id"] = len(det) + 1 - det["id"]  # numeracao de um processamento anterior
    det.to_file(run / "defeitos.geojson", driver="GeoJSON")
    revisao.gravar(run, {"defeitos": {"1": {"situacao": "falso_positivo", "quando": "x"}},
                         "adicionados": []})  # fmt: skip
    antes = revisao.caminho(run).read_text()

    def trava(*a, **k):
        raise PermissionError("resumo.xlsx aberto em outro programa")

    monkeypatch.setattr(exportar, "gravar_xlsx", trava)
    with warnings.catch_warnings(), pytest.raises(PermissionError):
        warnings.simplefilter("ignore")
        pipeline.processar(cena / "orto.tif", cena / "pista.geojson", run)
    assert revisao.caminho(run).read_text() == antes
    assert list(gpd.read_file(run / "defeitos.geojson")["id"]) == list(det["id"])
