"""Identidade visual: faixa com os logos, CSS dos dois temas, onca de simbolo e de carregamento."""

from pathlib import Path

from paviscan import marca


def test_faixa_tem_os_dois_logos_e_escapa_o_titulo():
    h = marca.faixa("Via <b>1</b>", "detalhe <b>pronto</b>")
    assert h.count("<img ") == 2
    assert h.index("Exército Brasileiro") < h.index("<h1>") < h.index("2º Grupamento de Engenharia")
    assert "Via &lt;b&gt;1&lt;/b&gt;" in h  # titulo vem de fora: escapado
    assert "detalhe <b>pronto</b>" in h  # detalhe ja vem montado por quem chama


def test_arquivos_de_marca_estao_no_repositorio():
    assert marca.data_url("exercito.png").startswith("data:image/png;base64,")
    assert marca.data_url("gpte2.svg").startswith("data:image/svg+xml;base64,")
    assert marca.data_url("onca.svg").startswith("data:image/svg+xml;base64,")
    assert marca.data_url("nao_existe.png") is None
    assert Path(marca.icone()).is_file()  # icone da aba do navegador


def test_css_tem_cor_para_os_dois_temas_e_o_indicador_de_carregamento():
    css = marca.css()
    assert css.strip().startswith("<style>") and css.strip().endswith("</style>")
    assert "light-dark(" in css  # uma cor para o tema claro, outra para o escuro
    assert 'data-test-script-state="running"' in css
    assert "stSpinnerIcon" in css
    assert css.count('url("data:image/svg+xml') == 1  # a onca entra uma vez so, em variavel


def test_onca_e_o_simbolo_do_programa():
    assert "marca-onca" in marca.titulo_app() and "PaviScan" in marca.titulo_app()
    assert ".marca-onca" in marca.css()


def test_sem_o_arquivo_da_onca_fica_so_o_anel(monkeypatch, tmp_path):
    monkeypatch.setattr(marca, "PASTA", tmp_path)  # pasta vazia: nenhum arquivo de marca
    marca.data_url.cache_clear()
    try:
        css = marca.css()
        assert "onca-gira" in css  # o anel continua
        # nada da onca: sem mascara, o enfeite viraria um disco cheio por cima do anel
        assert "onca-img" not in css and "stSpinnerIcon" not in css
        assert "marca-onca" not in marca.titulo_app() and "PaviScan" in marca.titulo_app()
        assert marca.icone() == "🛣️"
        assert "<img" not in marca.faixa("PaviScan")  # sem logo, a faixa sai so com o texto
    finally:
        marca.data_url.cache_clear()
