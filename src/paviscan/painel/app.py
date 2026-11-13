"""Dashboard do PaviScan. So interface: as contas vem de painel.dados e de orcamento."""

from __future__ import annotations

import html
import math
import os
from pathlib import Path

import pandas as pd
import plotly.express as px
import pydeck as pdk
import streamlit as st

from paviscan import acervo, base_publica, marca, orcamento, relatorio, revisao
from paviscan.painel import dados
from paviscan.painel.dados import (
    CONCEITOS,
    ROTULO_CLASSE,
    ROTULO_SEVERIDADE,
    ROTULO_SITUACAO,
    SEVERIDADES,
    fmt,
)

COR = {"baixa": "#E3B23C", "media": "#EE7B30", "alta": "#D62839"}
COR_RGB = {k: [int(v[i : i + 2], 16) for i in (1, 3, 5)] for k, v in COR.items()}
COR_CONCEITO = relatorio.COR_CONCEITO
COR_CONCEITO_RGB = {k: [int(v[i : i + 2], 16) for i in (1, 3, 5)] for k, v in COR_CONCEITO.items()}
RUNS = Path(os.environ.get("PAVISCAN_RUNS", "runs"))
ALTURA_MAPA = 620
SEM_BARRA = {"displayModeBar": False}
ABAS = [
    "Visão geral", "Vias e trechos", "Inspeção", "Orçamento", "Dados e relatório",
    "Nova análise", "Próximos passos",
]  # fmt: skip

st.set_page_config(page_title="PaviScan", page_icon="🛣️", layout="wide")
st.markdown(marca.css(), unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def _carregar(run: str, carimbo: float):
    return dados.carregar(Path(run))


@st.cache_data(show_spinner="Preparando a ortofoto...")
def _imagem(run: str, carimbo: float) -> str | None:
    return dados.previa_data_url(Path(run))


@st.cache_data(show_spinner=False)
def _params() -> dict:
    return dados.parametros()


@st.cache_data(show_spinner=False)
def _tabelas_orcamento(carimbo: float):
    return orcamento.carregar_tabelas()


def _carimbo_tabelas() -> float:
    """Muda quando config/solucoes.csv ou config/precos.csv e editado."""
    from paviscan import config as config_mod

    pasta = config_mod.localizar().parent
    return max((pasta / n).stat().st_mtime for n in ("solucoes.csv", "precos.csv"))


def _carimbo(run: Path) -> float:
    return (run / "resumo.json").stat().st_mtime


def _carimbo_rev(run: Path) -> float:
    arq = revisao.caminho(run)
    return arq.stat().st_mtime if arq.is_file() else 0.0


@st.cache_data(show_spinner=False)
def _revisado(run: str, carimbo: float, carimbo_rev: float):
    """Defeitos com a revisao manual aplicada por cima do que o detector gravou."""
    det, seg, _ = dados.carregar(Path(run))
    faixas = dados.parametros()["severidade"]
    return revisao.aplicar(det, seg, revisao.carregar(Path(run)), faixas)


def _limpar(chaves: list[str]) -> None:
    for k in chaves:
        st.session_state.pop(k, None)


def _selo(conceito: str) -> str:
    return f"<span class='selo' style='background:{COR_CONCEITO[conceito]}'>{conceito}</span>"


def _zoom(lon0: float, lat0: float, lon1: float, lat1: float, largura_px: int) -> float:
    """Zoom que enquadra o retangulo num mapa de `largura_px` x ALTURA_MAPA."""
    por_lon = math.log2(largura_px * 360 / (512 * max(lon1 - lon0, 1e-7)))
    cos_lat = math.cos(math.radians((lat0 + lat1) / 2))
    por_lat = math.log2(ALTURA_MAPA * 360 * cos_lat / (512 * max(lat1 - lat0, 1e-7)))
    return max(1.0, min(por_lon, por_lat) - 0.15)


def _dica_defeito(linha) -> str:
    return (
        f"<b>#{int(linha.id)} · {ROTULO_CLASSE.get(linha.classe, linha.classe)}</b><br/>"
        f"Severidade {ROTULO_SEVERIDADE[linha.severidade].lower()} · {fmt(linha.area_m2)} m²<br/>"
        f"{fmt(linha.largura_m)} × {fmt(linha.comprimento_m)} m · confiança {fmt(linha.confianca)}<br/>"
        f"{html.escape(str(linha.via))} · segmento {int(linha.segmento)}"
    )


def _mapa(det, seg, cond_seg, resumo, imagem, mostrar_seg: bool, mostrar_orto: bool, relevo: bool):
    (lat_s, lon_o), (lat_n, lon_l) = resumo["previa_limites"]
    camadas = []
    if mostrar_orto and imagem:
        camadas.append(
            pdk.Layer(
                "BitmapLayer",
                id="ortofoto",
                image=f'"{imagem}"',  # entre aspas: o pydeck trata como texto, nao como expressao
                bounds=[lon_o, lat_s, lon_l, lat_n],
            )
        )
    if (mostrar_seg or relevo) and len(seg):
        s = seg.merge(
            cond_seg[
                ["segmento", "n_buracos", "area_buracos_m2", "pct_afetada", "indice", "conceito"]
            ],
            on="segmento",
            suffixes=("_antigo", ""),
        )
        teto = max(float(s["pct_afetada"].max()), 1e-9)
        s["cor"] = [[*COR_CONCEITO_RGB[c], 150 if relevo else 95] for c in s["conceito"]]
        s["altura"] = [25.0 * v / teto for v in s["pct_afetada"]]
        s["dica"] = [
            f"<b>Segmento {int(i)}</b> · {html.escape(str(via))}<br/>Conceito {c} (índice {fmt(n, 0)})<br/>"
            f"{int(q)} defeito(s) · {fmt(a)} m² ({fmt(p)} % do segmento)"
            for i, via, c, n, q, a, p in zip(
                s["segmento"], s["via"], s["conceito"], s["indice"], s["n_buracos"],
                s["area_buracos_m2"], s["pct_afetada"], strict=True,
            )
        ]  # fmt: skip
        camadas.append(
            pdk.Layer(
                "GeoJsonLayer",
                id="segmentos",
                data=dados.para_mapa(s, ["segmento", "cor", "dica", "altura"]),
                get_fill_color="properties.cor",
                get_line_color=[255, 255, 255, 190],
                get_line_width=0.05,  # metros; o minimo em pixel vale com o mapa afastado
                line_width_min_pixels=1,
                extruded=relevo,
                get_elevation="properties.altura",
                pickable=True,
            )
        )
    if len(det):
        d = det.copy()
        d["cor"] = [[*COR_RGB[s], 110] for s in d["severidade"]]
        d["cor_linha"] = [[*COR_RGB[s], 255] for s in d["severidade"]]
        d["dica"] = [_dica_defeito(linha) for linha in d.itertuples()]
        camadas.append(
            pdk.Layer(
                "GeoJsonLayer",
                id="buracos",
                data=dados.para_mapa(d, ["id", "cor", "cor_linha", "dica"]),
                get_fill_color="properties.cor",
                get_line_color="properties.cor_linha",
                get_line_width=0.03,
                line_width_min_pixels=1.5,
                pickable=True,
            )
        )
        # anel em volta de cada defeito: aparece mesmo com o mapa afastado
        pontos = pd.DataFrame(
            {
                "id": d["id"],
                "lon": d["lon"],
                "lat": d["lat"],
                "raio": d["comprimento_m"] * 0.75 + 0.3,
                "cor_linha": d["cor_linha"],
                "dica": d["dica"],
            }
        )
        camadas.append(
            pdk.Layer(
                "ScatterplotLayer",
                id="aneis",
                data=pontos,
                get_position="[lon, lat]",
                get_radius="raio",
                radius_min_pixels=5,
                stroked=True,
                filled=False,
                get_line_color="cor_linha",
                get_line_width=0.03,
                line_width_min_pixels=1.5,
                pickable=True,
            )
        )
    if len(seg):
        oeste, sul, leste, norte = seg.to_crs(4326).total_bounds
    else:  # recorte vazio: enquadra a imagem inteira
        oeste, sul, leste, norte = lon_o, lat_s, lon_l, lat_n
    vista = pdk.ViewState(
        longitude=(oeste + leste) / 2,
        latitude=(sul + norte) / 2,
        zoom=_zoom(oeste, sul, leste, norte, 700),
        max_zoom=24,
        pitch=50 if relevo else 0,
        bearing=-15 if relevo else 0,
    )
    estilo = {"backgroundColor": marca.OLIVA_ESCURO, "color": "white", "fontSize": "13px"}
    return pdk.Deck(
        layers=camadas,
        initial_view_state=vista,
        map_style=None,  # sem estilo fixo, o Streamlit usa o mapa de fundo do tema ativo
        tooltip={"html": "{dica}", "style": estilo},
    )


def _selecionado(evento) -> int | None:
    """Id do defeito clicado no mapa, se houver."""
    try:
        objetos = evento.selection.objects
    except AttributeError:
        return None
    for camada in ("buracos", "aneis"):
        for obj in objetos.get(camada, []):
            ident = obj.get("id", obj.get("properties", {}).get("id"))
            if ident is not None:
                return int(ident)
    return None


def _ficha(linha, run: Path) -> None:
    recorte = dados.recorte(run, linha.id)
    if recorte is not None:
        st.image(str(recorte), width="stretch")
    cor = COR[linha.severidade]
    st.markdown(
        f"**#{int(linha.id)} · {ROTULO_CLASSE.get(linha.classe, linha.classe)}** · "
        f"<span style='color:{cor};font-weight:600'>{ROTULO_SEVERIDADE[linha.severidade]}</span>",
        unsafe_allow_html=True,
    )
    st.caption(
        f"{fmt(linha.area_m2)} m² · {fmt(linha.largura_m)} × {fmt(linha.comprimento_m)} m · "
        f"confiança {fmt(linha.confianca)}  \n{linha.via} · segmento {int(linha.segmento)}"
    )


def _orcar(det, seg, cfg_orc: dict, precos: pd.DataFrame, solucoes: pd.DataFrame) -> dict:
    """Orcamento do recorte atual com os parametros da tela."""
    orc = orcamento.orcar(det, seg, solucoes, precos, cfg_orc)
    plano, escolha = orcamento.plano_recomendado(det, orc, cfg_orc)
    bdi = float(cfg_orc["bdi"])
    return {
        "orc": orc,
        "plano": plano,
        "escolha": escolha,
        "bdi": bdi,
        "total": orcamento.total(plano, bdi),
        "por_opcao": orcamento.totais(orc, bdi),
        "por_servico": orcamento.por_servico(plano),
        "por_via": orcamento.por_via(plano, det, seg),
        "placeholder": bool(precos["placeholder"].any()),
        "seg_c": orcamento.trechos_a_recapear(orc),
    }


# ============================================================== revisao manual
FILAS = {
    "Pendentes": "pendente",
    "Todas": None,
    "Confirmadas": "confirmado",
    "Falsos positivos": "falso_positivo",
    "Adicionadas à mão": "manual",
}


def _marcar(run: Path, ident: int, situacao: str) -> None:
    revisao.marcar(run, ident, situacao)


def _desfazer(run: Path, ident: int) -> None:
    revisao.desfazer(run, ident)
    (run / "recortes" / f"rev_{int(ident)}.jpg").unlink(missing_ok=True)


def _remover(run: Path, ident: int) -> None:
    revisao.remover(run, ident)
    (run / "recortes" / f"rev_{int(ident)}.jpg").unlink(missing_ok=True)


@st.cache_data(show_spinner="Lendo a ortofoto em resolução cheia...", max_entries=24)
def _janela(orto: str, limites: tuple, carimbo: float):
    return revisao.janela_de_edicao(Path(orto), limites, 1200, dados.parametros()["raster"])


def _editor(orto: Path, limites: tuple, det_todos, editar, chave: str) -> list[dict]:
    """Mapa com a ortofoto em resolucao cheia para desenhar ou corrigir poligonos.

    `editar`: geometria (no SRC do levantamento) que entra ja editavel, ou None.
    Devolve os poligonos desenhados ou editados, como GeoJSON no SRC do levantamento.

    O mapa trabalha em coordenadas locais do recorte (0 a 1 no lado maior), nao em
    latitude e longitude: o componente arredonda as coordenadas na sexta casa, o que em
    graus daria 11 cm de erro e aqui da centesimos de milimetro. De quebra, a imagem
    fica exatamente alinhada ao SRC projetado.
    """
    try:
        import folium
        from folium.plugins import Draw
        from shapely import affinity
        from shapely.geometry import mapping, shape
        from streamlit_folium import st_folium
    except ImportError:
        st.error("Para desenhar na tela instale: pip install folium streamlit-folium")
        return []
    limites = tuple(round(float(v), 2) for v in limites)
    try:
        img, (x0, y0, x1, y1) = _janela(str(orto), limites, orto.stat().st_mtime)
    except (ValueError, OSError) as erro:
        st.error(f"Não consegui ler a ortofoto: {erro}")
        return []
    lado = max(x1 - x0, y1 - y0)

    def para_tela(geom):
        return affinity.affine_transform(geom, [1 / lado, 0, 0, 1 / lado, -x0 / lado, -y0 / lado])

    def da_tela(geom):
        return affinity.affine_transform(geom, [lado, 0, 0, lado, x0, y0])

    lim = [[0.0, 0.0], [(y1 - y0) / lado, (x1 - x0) / lado]]
    mapa = folium.Map(
        location=[lim[1][0] / 2, lim[1][1] / 2], tiles=None, max_zoom=14, zoom_snap=0.25
    )
    folium.raster_layers.ImageOverlay(image=img, bounds=lim, opacity=1.0).add_to(mapa)
    mapa.fit_bounds(lim)
    caixa = det_todos.cx[x0:x1, y0:y1]
    if editar is not None:
        caixa = caixa[~caixa.geometry.geom_equals(editar)]
    if len(caixa):  # as outras areas ja marcadas ali, so para referencia
        outras = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {}, "geometry": mapping(para_tela(g))}
                for g in caixa.geometry
            ],
        }
        folium.GeoJson(
            outras,
            style_function=lambda _: {"color": "#FFD60A", "weight": 2, "fillOpacity": 0.05},
            interactive=False,
        ).add_to(mapa)
    grupo = folium.FeatureGroup(name="edicao")
    if editar is not None:
        local = para_tela(editar)
        for parte in local.geoms if hasattr(local, "geoms") else [local]:
            folium.Polygon(
                locations=[(y, x) for x, y in parte.exterior.coords],
                color="#D62839", weight=3, fill_opacity=0.08,
            ).add_to(grupo)  # fmt: skip
    grupo.add_to(mapa)
    so_poligono = {k: False for k in ("polyline", "rectangle", "circle", "marker", "circlemarker")}
    so_poligono["polygon"] = {
        "shapeOptions": {"color": "#D62839", "weight": 3, "fillOpacity": 0.08}
    }
    Draw(
        feature_group=grupo, draw_options=so_poligono, edit_options={"edit": True, "remove": True}
    ).add_to(mapa)
    saida = st_folium(mapa, height=560, width=None, returned_objects=["all_drawings"], key=chave)
    desenhos = (saida or {}).get("all_drawings") or []
    return [
        mapping(da_tela(shape(d["geometry"])))
        for d in desenhos
        if d.get("geometry", {}).get("type") in ("Polygon", "MultiPolygon")
    ]


def _refazer_recortes(run: Path, orto: Path, ids: list[int], det_bruto, seg, params: dict) -> None:
    """Miniatura nova, com o contorno revisado, para as areas corrigidas ou adicionadas."""
    atual = revisao.aplicar(det_bruto, seg, revisao.carregar(run), params["severidade"])
    for ident in ids:
        linha = atual[atual["id"] == ident]
        if len(linha):
            try:
                revisao.recorte_do_defeito(run, orto, ident, linha.geometry.iloc[0], params)
            except (ValueError, OSError) as erro:  # a revisao ja esta salva; so a miniatura falhou
                st.warning(
                    f"A área #{ident} foi salva, mas a miniatura não pôde ser refeita: {erro}"
                )


# ============================================================== entrada de arquivos
def _mapa_de_desenho(img: acervo.Imagem):
    """Mapa com a previa da ortofoto para o usuario tracar o eixo da via. Devolve as linhas."""
    try:
        import folium
        from folium.plugins import Draw
        from streamlit_folium import st_folium
    except ImportError:
        st.error("Para desenhar na tela instale: pip install folium streamlit-folium")
        return []
    with st.spinner("Preparando a prévia da imagem..."):
        png, ((lat_s, lon_o), (lat_n, lon_l)) = acervo.previa_rapida(img)
    mapa = folium.Map(location=[(lat_s + lat_n) / 2, (lon_o + lon_l) / 2], tiles=None, max_zoom=24)
    folium.TileLayer("OpenStreetMap", max_native_zoom=19, max_zoom=24).add_to(mapa)
    limites = [[lat_s, lon_o], [lat_n, lon_l]]
    folium.raster_layers.ImageOverlay(image=str(png), bounds=limites, opacity=1.0).add_to(mapa)
    mapa.fit_bounds(limites)
    so_linha = {k: False for k in ("polygon", "rectangle", "circle", "marker", "circlemarker")}
    so_linha["polyline"] = {"shapeOptions": {"color": "#D62839", "weight": 4}}
    Draw(draw_options=so_linha, edit_options={"edit": True, "remove": True}).add_to(mapa)
    saida = st_folium(
        mapa, height=520, width=None, returned_objects=["all_drawings"], key=f"desenho:{img.nome}"
    )
    desenhos = (saida or {}).get("all_drawings") or []
    return [d["geometry"] for d in desenhos if d.get("geometry", {}).get("type") == "LineString"]


def _nova_analise() -> None:
    """Entrada de arquivos: guardar a imagem no acervo, indicar a via e processar."""
    from paviscan import pipeline, pista_osm

    pasta = acervo.pasta_imagens()
    st.markdown("<div class='passo'>1 · Imagem</div>", unsafe_allow_html=True)
    aviso = st.session_state.pop("aviso_acervo", None)
    if aviso:
        st.success(aviso)
    imagens = acervo.listar(pasta)
    c_sel, c_add = st.columns([1, 1.3], gap="large")
    with c_add, st.expander("Adicionar imagem ao acervo", expanded=not imagens):
        n_envio = st.session_state.get(
            "n_envio", 0
        )  # muda a chave e limpa o campo depois de enviar
        arq = st.file_uploader(
            "Ortofoto (GeoTIFF georreferenciado)",
            type=["tif", "tiff", "geotiff"],
            key=f"envio:{n_envio}",
        )
        caminho = st.text_input(
            "ou caminho de um arquivo no disco",
            placeholder="/caminho/para/ortofoto.tif",
            help="Para imagem muito grande, apontar o caminho evita carregar o arquivo pelo navegador.",
            key=f"caminho:{n_envio}",
        )
        sugestao = Path(arq.name).stem if arq else (Path(caminho).stem if caminho else "")
        nome_img = st.text_input("Nome no acervo", value=acervo.nome_seguro(sugestao))
        existe = bool(nome_img) and (pasta / acervo.nome_seguro(nome_img) / "orto.tif").exists()
        trocar = st.checkbox(f"Já existe '{nome_img}'. Substituir?") if existe else False
        pode = bool(nome_img) and (arq is not None or bool(caminho)) and (not existe or trocar)
        if st.button("Guardar no acervo", type="primary", disabled=not pode):
            origem = arq if arq is not None else Path(caminho).expanduser()
            if arq is None and not origem.is_file():
                st.error("Arquivo não encontrado nesse caminho.")
            else:
                try:
                    with st.spinner("Gravando e conferindo a georreferência..."):
                        nova = acervo.guardar(origem, nome_img, pasta, substituir=trocar)
                except (ValueError, FileExistsError, OSError) as erro:
                    st.error(f"Não foi possível guardar: {erro}")
                else:
                    st.session_state["n_envio"] = n_envio + 1
                    st.session_state["imagem_escolhida"] = nova.nome
                    st.session_state["aviso_acervo"] = f"Imagem '{nova.nome}' guardada no acervo."
                    st.rerun()
        st.caption(
            f"O acervo fica em `{pasta}`. Copiar uma pasta com `orto.tif` para lá dá no mesmo."
        )
    with c_sel:
        if not imagens:
            st.info("O acervo está vazio. Adicione uma ortofoto ao lado.")
            return
        nomes = [i.nome for i in imagens]
        if st.session_state.get("imagem_escolhida") not in nomes:
            st.session_state["imagem_escolhida"] = nomes[0]
        st.selectbox("Imagem do acervo", nomes, key="imagem_escolhida")
        img = imagens[nomes.index(st.session_state["imagem_escolhida"])]
        try:
            meta = acervo.metadados(img.orto)
        except Exception as erro:  # arquivo corrompido ou ilegivel: mostra e nao derruba o painel
            st.error(f"Não consegui ler a imagem: {erro}")
            return
        st.caption(
            f"{meta['largura_px']} × {meta['altura_px']} px · GSD {fmt(meta['gsd_cm'], 1)} cm/px · "
            f"{meta['src']} · {fmt(meta['tamanho_mb'], 0)} MB"
            + (f" · será reprojetada para {meta['src_trabalho']}" if meta["reprojetada"] else "")
        )
        gsd_aviso = float(dados.parametros()["acervo"]["gsd_aviso_cm"])
        if meta["gsd_cm"] > gsd_aviso:
            st.warning(
                f"GSD acima de {fmt(gsd_aviso, 0)} cm/px: buracos pequenos (30 cm) ficam abaixo do que o método enxerga."
            )

    st.markdown("<div class='passo'>2 · Onde está a via</div>", unsafe_allow_html=True)
    opcoes = [
        "Automático (ruas do OpenStreetMap)",
        "Desenhar o eixo da via na imagem",
        "Enviar arquivo",
    ]
    if img.pista:
        opcoes.insert(0, "Usar a pista já guardada")
    modo = st.radio("Como indicar a via", opcoes, horizontal=True, label_visibility="collapsed")
    c1, c2 = st.columns([1, 1])
    superficie = c1.selectbox(
        "Superfície",
        ["auto", "asfalto", "terra"],
        format_func={
            "auto": "Automática (decide por via: asfalto ou terra)",
            "asfalto": "Asfalto em bom estado de cor (cinza)",
            "terra": "Via muito gasta ou de terra",
        }.get,
        help="Define como a borda da via é achada em volta do eixo.",
        disabled=modo == "Usar a pista já guardada",
    )
    linhas = []
    if modo.startswith("Automático"):
        c2.caption(
            "A consulta envia o retângulo geográfico da imagem ao servidor público do OpenStreetMap. "
            "Em área sem ruas mapeadas, desenhe o eixo."
        )
    elif modo.startswith("Desenhar"):
        st.caption(
            "Use a ferramenta de linha (canto superior esquerdo) e trace o meio da via. Pode traçar várias."
        )
        linhas = _mapa_de_desenho(img)
        st.caption(
            f"{len(linhas)} linha(s) traçada(s)."
            + (" Eixo guardado anterior será substituído." if img.eixo else "")
        )
    elif modo.startswith("Enviar"):
        vetor = st.file_uploader(
            "Polígono da pista ou eixo da via (GeoJSON ou GPKG)",
            type=["geojson", "json", "gpkg"],
            help="Polígonos são usados como pista. Linhas são usadas como eixo, e a pista é gerada em volta.",
        )
        if vetor is not None and st.button("Guardar vetor"):
            try:
                qual = acervo.guardar_vetor(vetor, img, Path(vetor.name).suffix or ".geojson")
            except ValueError as erro:
                st.error(str(erro))
            else:
                st.session_state["aviso_acervo"] = f"Arquivo guardado como {qual} de '{img.nome}'."
                st.rerun()

    st.markdown("<div class='passo'>3 · Processar</div>", unsafe_allow_html=True)
    c1, c2 = st.columns([1, 1])
    nome_run = acervo.nome_seguro(c1.text_input("Nome do levantamento", value=img.nome))
    existe = bool(nome_run) and (RUNS / nome_run / "resumo.json").exists()
    refazer = c2.checkbox(f"Já existe '{nome_run}'. Refazer?") if existe else False
    falta_traco = modo.startswith("Desenhar") and not linhas and not img.eixo
    falta_vetor = modo.startswith("Enviar") and not (img.pista or img.eixo)
    pode = bool(nome_run) and (not existe or refazer) and not falta_traco and not falta_vetor
    if falta_traco:
        st.caption("Trace pelo menos uma linha sobre a via para liberar o processamento.")
    if falta_vetor:
        st.caption("Envie e guarde o arquivo da pista ou do eixo para liberar o processamento.")
    if not st.button("Processar", type="primary", disabled=not pode):
        return
    barra = st.progress(0.0, text="Preparando")
    try:
        if modo.startswith("Desenhar") and linhas:
            acervo.gravar_eixo(img, linhas)
        pista = img.pasta / "pista.geojson"
        usar_guardada = modo == "Usar a pista já guardada" or (
            modo.startswith("Enviar") and img.pista
        )
        if not usar_guardada or not pista.is_file():
            barra.progress(0.03, text="Gerando a pista em volta do eixo")
            eixo = None if modo.startswith("Automático") else img.eixo
            pista_osm.gerar(img.orto, pista, eixo=eixo, superficie=superficie)
        pipeline.processar(
            img.orto,
            pista,
            RUNS / nome_run,
            progresso=lambda f, t: barra.progress(min(1.0, f), text=t),
        )
    except Exception as erro:  # mostra o motivo na tela em vez de derrubar o painel
        st.error(f"Não foi possível processar: {erro}")
        with st.expander("Detalhe técnico"):
            st.exception(erro)
        return
    # chaves de controle nao podem ser alteradas depois que o controle foi desenhado nesta
    # execucao: a escolha vai por chaves "pendentes", aplicadas no inicio da proxima
    st.session_state["levantamento_pendente"] = nome_run
    st.session_state["aba_pendente"] = ABAS[0]
    st.cache_data.clear()
    st.rerun()


# ============================================================== barra lateral
with st.sidebar:
    st.markdown("## 🛣️ PaviScan")
    st.caption("Condição do pavimento a partir de ortofoto de drone")
    runs = dados.listar_runs(RUNS)
    if not runs:
        st.info("Nenhum levantamento ainda. Use **Nova análise** para processar a primeira imagem.")
if not runs:
    st.markdown(
        marca.faixa(
            "PaviScan",
            "Envie uma ortofoto, indique a via e veja os defeitos medidos, a condição por trecho "
            "e a estimativa de reparo.",
        ),
        unsafe_allow_html=True,
    )
    _nova_analise()
    st.stop()

with st.sidebar:
    nomes = [r.name for r in runs]
    pendente = st.session_state.pop("levantamento_pendente", None)
    if pendente in nomes:  # levantamento recem-processado: abre nele
        st.session_state["levantamento"] = pendente
    if st.session_state.get("levantamento") not in nomes:
        st.session_state["levantamento"] = nomes[0]
    if len(runs) > 1:
        st.selectbox("Levantamento", nomes, key="levantamento")
    run = runs[nomes.index(st.session_state["levantamento"])]
    det_bruto, seg, resumo = _carregar(str(run), _carimbo(run))
    det_todos = _revisado(str(run), _carimbo(run), _carimbo_rev(run))

    st.markdown("### Filtros")
    pre = f"{run.name}:"
    chaves = [pre + k for k in ("classe", "area", "sev", "conf", "vias", "segs", "sit")]
    area_min = area_max = conf_min = sev = vias = segs = classes = situacoes = None
    if len(det_todos):
        tipos = [c for c in ROTULO_CLASSE if c in set(det_todos["classe"])]
        tipos += sorted(set(det_todos["classe"]) - set(tipos))
        if len(tipos) > 1:
            escolha = st.multiselect(
                "Tipo de defeito", tipos, default=tipos, key=chaves[0],
                format_func=lambda c: ROTULO_CLASSE.get(c, c),
            )  # fmt: skip
            classes = escolha if len(escolha) < len(tipos) else None
        a0 = math.floor(float(det_todos["area_m2"].min()) * 100) / 100
        a1 = math.ceil(float(det_todos["area_m2"].max()) * 100) / 100
        if a1 > a0:
            lo, hi = st.slider("Área (m²)", a0, a1, (a0, a1), step=0.01, key=chaves[1])
            area_min = lo if lo > a0 else None
            area_max = hi if hi < a1 else None
        presentes = [s for s in SEVERIDADES if s in set(det_todos["severidade"])]
        escolha = st.multiselect(
            "Severidade", presentes, default=presentes, format_func=ROTULO_SEVERIDADE.get,
            key=chaves[2],
        )  # fmt: skip
        sev = escolha if len(escolha) < len(presentes) else None
        conf = st.slider("Confiança mínima", 0.0, 1.0, 0.0, step=0.05, key=chaves[3])
        conf_min = conf if conf > 0 else None
        nomes_vias = sorted(seg["via"].dropna().unique())
        if len(nomes_vias) > 1:
            vias = st.multiselect("Vias", nomes_vias, placeholder="Todas", key=chaves[4]) or None
        opcoes = sorted(int(s) for s in seg["segmento"])
        if vias:
            opcoes = sorted(int(s) for s in seg.loc[seg["via"].isin(vias), "segmento"])
        segs = st.multiselect("Segmentos", opcoes, placeholder="Todos", key=chaves[5]) or None
        # falso positivo fica fora dos numeros, a menos que o usuario peca para incluir
        situacoes = [x for x in revisao.SITUACOES if x != "falso_positivo"]
        if (det_todos["revisao"] == "falso_positivo").any():
            incluir = st.toggle(
                "Incluir falsos positivos",
                value=False,
                key=chaves[6],
                help="Áreas marcadas como falso positivo na revisão ficam fora dos indicadores, do orçamento e do relatório.",
            )
            situacoes = None if incluir else situacoes
        st.button("Limpar filtros", on_click=_limpar, args=(chaves,), width="stretch")
    else:
        st.caption("Nenhum defeito detectado neste levantamento.")

params = _params()
det = dados.filtrar(det_todos, area_min, area_max, conf_min, sev, segs, vias, classes, situacoes)
seg_f = dados.filtrar_segmentos(seg, segs, vias)  # recorte de via/segmento escolhido
validos = dados.filtrar(det_todos, situacoes=situacoes)  # tudo, menos o que a revisao descartou
filtrado = len(det) < len(validos)
ind = dados.resumir(det, seg_f)
cond = dados.condicao_geral(det, seg_f, params["condicao"])
cond_seg = dados.condicao_segmentos(det, seg_f, params["condicao"])
tab_vias = dados.por_via(det, seg_f, params["condicao"])

# orcamento do recorte, com os parametros escolhidos na aba Orcamento (ou os do params.yaml)
solucoes, precos_base = _tabelas_orcamento(_carimbo_tabelas())
cfg_orc = dict(params["orcamento"])
# o Streamlit apaga o estado de um controle que nao foi desenhado; por isso a ultima escolha
# fica guardada em "orc_salvo" e vale tambem com a secao Orcamento fechada
salvo = st.session_state.setdefault(
    "orc_salvo",
    {
        "pavimento": cfg_orc["pavimento"],
        "terreno": cfg_orc["terreno"],
        "bdi": 100 * cfg_orc["bdi"],
        "margem": float(cfg_orc["margem_corte_m"]),
        "limite": 100 * cfg_orc["limite_recapeamento"],
    },
)
for nome in salvo:
    salvo[nome] = st.session_state.get(f"orc_{nome}", salvo[nome])
cfg_orc["pavimento"], cfg_orc["terreno"] = salvo["pavimento"], salvo["terreno"]
cfg_orc["bdi"] = salvo["bdi"] / 100
cfg_orc["margem_corte_m"] = salvo["margem"]
cfg_orc["limite_recapeamento"] = salvo["limite"] / 100
precos = st.session_state.get("orc_precos_editados", precos_base)
orc = _orcar(det, seg_f, cfg_orc, precos, solucoes)

# ============================================================== cabecalho
fonte = resumo.get("fonte") or {}
pista_toda = dados.area_e_extensao(seg)
titulo = fonte.get("titulo") or run.name.replace("_", " ").title()
st.markdown(
    marca.faixa(
        titulo,
        f"Levantamento <b>{html.escape(run.name)}</b> · "
        f"GSD {fmt(100 * resumo['gsd_m'], 1)} cm/px · {fmt(pista_toda['area_pista_m2'], 0)} m² de pista em "
        f"{pista_toda['n_segmentos']} segmentos de {fmt(pista_toda['extensao_m'], 0)} m"
        + (f" · imagem de {html.escape(str(fonte['data']))}" if fonte.get("data") else ""),
    ),
    unsafe_allow_html=True,
)

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric(
    "Condição geral",
    f"{cond['conceito']} · {fmt(cond['indice'], 0)}",
    help="Índice de 0 a 100 e conceito de A a E. De trabalho: não é IGG nem ICP de norma.",
)
c2.metric("Defeitos", fmt(ind["n_buracos"], 0))
c3.metric("Área afetada", f"{fmt(ind['area_total_m2'], 1)} m²")
c4.metric("Pista afetada", f"{fmt(ind['pct_afetada'], 2)} %")
c5.metric(
    "Reparo estimado",
    dados.reais(orc["total"]["total"]).replace(",00", ""),
    help="Plano recomendado, com BDI. Preços ilustrativos até serem substituídos na aba Orçamento.",
)
if filtrado:
    st.caption(f"Filtro ativo: **{len(det)} de {len(validos)} defeitos**.")
if resumo.get("cortadas_por_janela"):
    st.warning(
        f"{resumo['cortadas_por_janela']} detecção(ões) grandes ficaram na emenda das janelas de "
        "processamento e podem estar truncadas (coluna `na_emenda` da tabela)."
    )
if len(det_todos) and det.empty:
    st.warning("Nenhum defeito passa nos filtros atuais. Afrouxe um filtro ou limpe todos.")

# navegacao com chave: a secao escolhida sobrevive a cada clique, e so ela e desenhada
aba_pendente = st.session_state.pop("aba_pendente", None)
if aba_pendente in ABAS:  # depois de processar, abre a visao geral do levantamento novo
    st.session_state["aba"] = aba_pendente
aba = st.segmented_control("Seção", ABAS, default=ABAS[0], key="aba", label_visibility="collapsed")
aba = aba or ABAS[0]

# ============================================================== visao geral
if aba == "Visão geral":
    col_mapa, col_lado = st.columns([3.2, 1], gap="medium")
    with col_mapa:
        o1, o2, o3, _ = st.columns([1, 1.5, 1.2, 1.5])
        mostrar_orto = o1.toggle("Ortofoto", value=True)
        mostrar_seg = o2.toggle("Condição por trecho", value=False)
        relevo = o3.toggle(
            "Relevo 3D", value=False, help="Altura do trecho proporcional à % afetada."
        )
        baralho = _mapa(
            det, seg_f, cond_seg, resumo, _imagem(str(run), _carimbo(run)),
            mostrar_seg, mostrar_orto, relevo,
        )  # fmt: skip
        evento = st.pydeck_chart(
            baralho,
            height=ALTURA_MAPA,
            on_select="rerun",
            selection_mode="single-object",
            key=f"mapa:{run.name}:{int(relevo)}",
        )
    with col_lado:
        escolhido = _selecionado(evento)
        linha = det[det["id"] == escolhido] if escolhido is not None else det.iloc[0:0]
        if len(linha):
            _ficha(next(linha.itertuples()), run)
        else:
            st.caption("Clique em um defeito no mapa para ver o recorte em resolução cheia.")
        if len(det):
            contagem = dados.contagem_severidade(det)
            fig = px.bar(
                x=[ROTULO_SEVERIDADE[s] for s in SEVERIDADES],
                y=[contagem[s] for s in SEVERIDADES],
                color=SEVERIDADES,
                color_discrete_map=COR,
                text=[contagem[s] for s in SEVERIDADES],
            )
            fig.update_layout(
                height=210, margin=dict(l=0, r=0, t=30, b=0), showlegend=False,
                title_text="Por severidade", title_font_size=14, xaxis_title=None, yaxis_title=None,
                yaxis_visible=False,
            )  # fmt: skip
            st.plotly_chart(fig, width="stretch", config=SEM_BARRA)
            por_tipo = dados.contagem_classe(det)
            if len(por_tipo) > 1:
                st.markdown("**Por tipo**")
                for classe, v in por_tipo.items():
                    st.caption(
                        f"{ROTULO_CLASSE.get(classe, classe)}: {v['n']} · {fmt(v['area_m2'], 1)} m²"
                    )

    prioridade = cond_seg[cond_seg["n_buracos"] > 0].sort_values("indice").head(4)
    if len(prioridade):
        st.subheader("Trechos prioritários")
        for coluna, trecho in zip(st.columns(4), prioridade.itertuples(), strict=False):
            do_trecho = det[det["segmento"] == trecho.segmento].sort_values(
                "area_m2", ascending=False
            )
            with coluna:
                recorte = dados.recorte(run, do_trecho["id"].iloc[0]) if len(do_trecho) else None
                if recorte is not None:
                    st.image(str(recorte), width="stretch")
                st.markdown(
                    f"<div class='cartao'>{_selo(trecho.conceito)} <b>Segmento {int(trecho.segmento)}</b><br/>"
                    f"<small>{html.escape(str(trecho.via))}</small><br/>{int(trecho.n_buracos)} defeito(s) · "
                    f"{fmt(trecho.area_buracos_m2, 1)} m² · {fmt(trecho.pct_afetada, 1)} % do trecho</div>",
                    unsafe_allow_html=True,
                )

# ============================================================== vias e trechos
if aba == "Vias e trechos":
    if len(tab_vias):
        g1, g2 = st.columns([1.1, 1], gap="large")
        with g1:
            st.subheader("Condição por via")
            st.dataframe(
                tab_vias[
                    [
                        "via",
                        "conceito",
                        "indice",
                        "extensao_m",
                        "n_buracos",
                        "area_buracos_m2",
                        "pct_afetada",
                    ]
                ],
                hide_index=True,
                width="stretch",
                column_config={
                    "via": "Via",
                    "conceito": "Conceito",
                    "indice": st.column_config.ProgressColumn(
                        "Índice", format="%.0f", min_value=0, max_value=100
                    ),
                    "extensao_m": st.column_config.NumberColumn("Extensão (m)", format="%.0f"),
                    "n_buracos": st.column_config.NumberColumn("Defeitos", format="%d"),
                    "area_buracos_m2": st.column_config.NumberColumn(
                        "Área afetada (m²)", format="%.1f"
                    ),
                    "pct_afetada": st.column_config.NumberColumn("% afetada", format="%.2f"),
                },
            )
        with g2:
            st.subheader("Índice de condição")
            ordem = tab_vias.sort_values("indice", ascending=False)
            fig = px.bar(
                ordem, x="indice", y="via", orientation="h", color="conceito",
                color_discrete_map=COR_CONCEITO, category_orders={"conceito": CONCEITOS},
                text=ordem["conceito"],
            )  # fmt: skip
            fig.update_layout(
                height=max(260, 26 * len(ordem) + 80), margin=dict(l=0, r=0, t=10, b=0),
                xaxis_title="Índice (0 a 100)", yaxis_title=None, legend_title="Conceito",
                xaxis_range=[0, 100], yaxis={"categoryorder": "total ascending"}, separators=",.",
            )  # fmt: skip
            st.plotly_chart(fig, width="stretch", config=SEM_BARRA)
    g1, g2 = st.columns(2, gap="large")
    with g1:
        st.subheader("Perfil ao longo da pista")
        fig = px.bar(
            cond_seg, x="segmento", y="pct_afetada", color="conceito",
            color_discrete_map=COR_CONCEITO, category_orders={"conceito": CONCEITOS},
            hover_data={"via": True, "n_buracos": True, "area_buracos_m2": ":.2f"},
        )  # fmt: skip
        fig.update_layout(
            height=320, margin=dict(l=0, r=0, t=10, b=0), separators=",.", legend_title="Conceito",
            xaxis_title="Segmento (ordem da pista)", yaxis_title="% do segmento afetada",
        )  # fmt: skip
        st.plotly_chart(fig, width="stretch", config=SEM_BARRA)
    with g2:
        st.subheader("Distribuição das áreas")
        if len(det):
            d = det.assign(Severidade=det["severidade"].map(ROTULO_SEVERIDADE))
            fig = px.histogram(
                d, x="area_m2", color="Severidade", nbins=30,
                color_discrete_map={ROTULO_SEVERIDADE[k]: v for k, v in COR.items()},
                category_orders={"Severidade": [ROTULO_SEVERIDADE[s] for s in SEVERIDADES]},
            )  # fmt: skip
            fig.update_layout(
                height=320, margin=dict(l=0, r=0, t=10, b=0), separators=",.", bargap=0.05,
                xaxis_title="Área do defeito (m²)", yaxis_title="Defeitos", legend_title=None,
            )  # fmt: skip
            st.plotly_chart(fig, width="stretch", config=SEM_BARRA)
    st.subheader("Piores segmentos")
    pior = cond_seg[cond_seg["n_buracos"] > 0].sort_values("indice").head(12)
    st.dataframe(
        pior[
            ["segmento", "via", "conceito", "indice", "n_buracos", "area_buracos_m2", "pct_afetada"]
        ],
        hide_index=True,
        width="stretch",
        column_config={
            "segmento": st.column_config.NumberColumn("Segmento", format="%d"),
            "via": "Via",
            "conceito": "Conceito",
            "indice": st.column_config.ProgressColumn(
                "Índice", format="%.0f", min_value=0, max_value=100
            ),
            "n_buracos": st.column_config.NumberColumn("Defeitos", format="%d"),
            "area_buracos_m2": st.column_config.NumberColumn("Área afetada (m²)", format="%.2f"),
            "pct_afetada": st.column_config.NumberColumn("% afetada", format="%.2f"),
        },
    )
    st.caption(
        "O índice (0 a 100) perde pontos por área afetada e por densidade de defeitos; os conceitos vão de A "
        "(bom) a E (crítico). É um índice de trabalho para ordenar trechos, não é IGG nem ICP de norma."
    )

# ============================================================== inspecao e revisao
if aba == "Inspeção":
    cont = revisao.contagem(det_todos)
    st.progress(
        cont["revisados"] / cont["do_detector"] if cont["do_detector"] else 0.0,
        text=(
            f"Revisão: {cont['revisados']} de {cont['do_detector']} detecções conferidas · "
            f"{cont['confirmado']} confirmadas · {cont['falso_positivo']} falsos positivos · "
            f"{cont['manual']} adicionadas à mão"
        ),
    )
    modo_rev = st.radio(
        "Modo de revisão",
        ["Triagem rápida", "Corrigir contorno", "Adicionar área que faltou"],
        horizontal=True,
        label_visibility="collapsed",
    )
    # a revisao enxerga tambem o que ja foi marcado como falso positivo
    det_insp = dados.filtrar(det_todos, area_min, area_max, conf_min, sev, segs, vias, classes)
    orto_run = dados.caminho_ortofoto(run, resumo)
    n_edicao = st.session_state.get(
        "n_edicao", 0
    )  # muda a chave e limpa o desenho depois de salvar

    if modo_rev == "Triagem rápida":
        st.caption(
            "Recorte em resolução cheia de cada área, com o contorno detectado. Marque ✓ no que é defeito "
            "e ✗ no que não é. Falso positivo sai dos indicadores, do orçamento e do relatório."
        )
        k1, k2, k3, _ = st.columns([1.1, 1.2, 1, 2.2])
        fila = k1.selectbox("Mostrar", list(FILAS))
        criterio = k2.selectbox("Ordenar por", ["Maior área", "Maior confiança", "Menor confiança"])
        quantos = k3.select_slider("Quantidade", [6, 12, 18, 24, 36], value=12)
        coluna, crescente = {
            "Maior área": ("area_m2", False),
            "Maior confiança": ("confianca", False),
            "Menor confiança": ("confianca", True),
        }[criterio]
        na_fila = det_insp if FILAS[fila] is None else det_insp[det_insp["revisao"] == FILAS[fila]]
        amostra = na_fila.sort_values(coluna, ascending=crescente).head(quantos)
        if amostra.empty:
            st.info("Nada nesta fila com os filtros atuais.")
        grade = st.columns(6)
        for i, item in enumerate(amostra.itertuples()):
            ident = int(item.id)
            with grade[i % 6]:
                recorte = dados.recorte(run, ident)
                if recorte is not None:
                    st.image(str(recorte), width="stretch")
                st.caption(
                    f"**#{ident}** · {fmt(item.area_m2)} m² · conf. {fmt(item.confianca)}  \n"
                    f"{ROTULO_CLASSE.get(item.classe, item.classe)} · **{ROTULO_SITUACAO[item.revisao]}**"
                )
                if item.revisao == "manual":
                    st.button(
                        "Remover",
                        key=f"rm:{run.name}:{ident}",
                        on_click=_remover,
                        args=(run, ident),
                    )
                    continue
                b1, b2, b3 = st.columns(3)
                b1.button(
                    "✓", key=f"ok:{run.name}:{ident}", help="É defeito", on_click=_marcar,
                    args=(run, ident, "confirmado"),
                    type="primary" if item.revisao == "confirmado" else "secondary",
                )  # fmt: skip
                b2.button(
                    "✗", key=f"fp:{run.name}:{ident}", help="Falso positivo", on_click=_marcar,
                    args=(run, ident, "falso_positivo"),
                    type="primary" if item.revisao == "falso_positivo" else "secondary",
                )  # fmt: skip
                b3.button(
                    "↺", key=f"un:{run.name}:{ident}", help="Voltar a pendente", on_click=_marcar,
                    args=(run, ident, "pendente"), disabled=item.revisao == "pendente",
                )  # fmt: skip

    elif orto_run is None:
        st.warning(
            "Para corrigir ou adicionar áreas é preciso a ortofoto original, e ela não foi encontrada "
            f"(`{resumo.get('ortofoto_caminho') or resumo.get('ortofoto', '')}`). A triagem rápida continua funcionando."
        )

    elif modo_rev == "Corrigir contorno":
        if det_insp.empty:
            st.info("Nenhuma área com os filtros atuais.")
        else:
            ordem = det_insp.sort_values("area_m2", ascending=False)
            rotulos = {
                int(
                    r.id
                ): f"#{int(r.id)} · {ROTULO_CLASSE.get(r.classe, r.classe)} · {fmt(r.area_m2)} m² · "
                f"{r.via} · {ROTULO_SITUACAO[r.revisao]}"
                for r in ordem.itertuples()
            }
            alvo = st.selectbox(
                "Área a corrigir", list(rotulos), format_func=rotulos.get, key=f"alvo:{run.name}"
            )
            linha = ordem[ordem["id"] == alvo].iloc[0]
            st.caption(
                "O contorno atual está em vermelho. Clique no ícone de editar (à esquerda do mapa) para arrastar "
                "os vértices, ou apague e desenhe um polígono novo. Depois clique em Salvar contorno. "
                "Em amarelo, as outras áreas já marcadas ali."
            )
            x0, y0, x1, y1 = linha.geometry.bounds
            m = 2 * float(params["recortes"]["margem_m"])
            desenhos = _editor(
                orto_run, (x0 - m, y0 - m, x1 + m, y1 + m), det_todos, linha.geometry,
                f"corrigir:{run.name}:{alvo}:{n_edicao}",
            )  # fmt: skip
            c1, c2, c3, c4 = st.columns([1.2, 1, 1.2, 1.6])
            if c1.button("Salvar contorno", type="primary", disabled=not desenhos, width="stretch"):
                try:
                    revisao.corrigir(run, alvo, desenhos)
                except ValueError as erro:
                    st.error(str(erro))
                else:
                    _refazer_recortes(run, orto_run, [int(alvo)], det_bruto, seg, params)
                    st.session_state["n_edicao"] = n_edicao + 1
                    st.rerun()
            if linha.revisao != "manual":
                c2.button(
                    "✓ É defeito",
                    on_click=_marcar,
                    args=(run, int(alvo), "confirmado"),
                    width="stretch",
                )
                c3.button(
                    "✗ Falso positivo",
                    on_click=_marcar,
                    args=(run, int(alvo), "falso_positivo"),
                    width="stretch",
                )
                if linha.revisao != "pendente" or linha.corrigido:
                    c4.button(
                        "Desfazer a revisão desta área",
                        on_click=_desfazer,
                        args=(run, int(alvo)),
                        width="stretch",
                    )
            else:
                c2.button("Remover área", on_click=_remover, args=(run, int(alvo)), width="stretch")

    else:  # adicionar area que faltou
        trechos = seg_f.sort_values("segmento")
        rotulos = {
            int(r.segmento): f"Segmento {int(r.segmento)} · {r.via}" for r in trechos.itertuples()
        }
        c1, c2 = st.columns([1.6, 1])
        seg_alvo = c1.selectbox(
            "Trecho onde falta uma área",
            list(rotulos),
            format_func=rotulos.get,
            key=f"segadd:{run.name}",
        )
        classe_nova = c2.selectbox(
            "Tipo de defeito", list(ROTULO_CLASSE), format_func=ROTULO_CLASSE.get
        )
        st.caption(
            "Use a ferramenta de polígono (à esquerda do mapa) e contorne cada área que faltou; pode desenhar "
            "várias. Em amarelo, as áreas que já existem. Depois clique em Adicionar."
        )
        x0, y0, x1, y1 = trechos[trechos["segmento"] == seg_alvo].geometry.iloc[0].bounds
        m = float(params["recortes"]["margem_m"])
        desenhos = _editor(
            orto_run, (x0 - m, y0 - m, x1 + m, y1 + m), det_todos, None,
            f"adicionar:{run.name}:{seg_alvo}:{n_edicao}",
        )  # fmt: skip
        if st.button(f"Adicionar {len(desenhos)} área(s)", type="primary", disabled=not desenhos):
            proximo = int(det_bruto["id"].max()) + 1 if len(det_bruto) else 1
            try:
                criados = revisao.adicionar(run, desenhos, classe_nova, proximo)
            except ValueError as erro:
                st.error(str(erro))
            else:
                _refazer_recortes(run, orto_run, criados, det_bruto, seg, params)
                st.session_state["n_edicao"] = n_edicao + 1
                st.rerun()
        manuais = det_todos[
            (det_todos["revisao"] == "manual") & (det_todos["segmento"] == seg_alvo)
        ]
        for r in manuais.itertuples():
            a1, a2 = st.columns([3, 1])
            a1.caption(
                f"**#{int(r.id)}** · {ROTULO_CLASSE.get(r.classe, r.classe)} · {fmt(r.area_m2)} m² · adicionada à mão"
            )
            a2.button(
                "Remover",
                key=f"rm2:{run.name}:{int(r.id)}",
                on_click=_remover,
                args=(run, int(r.id)),
            )

# ============================================================== orcamento
if aba == "Orçamento":
    if orc["placeholder"]:
        st.warning(
            "Os preços unitários são **ilustrativos**. Substitua pelos de referência (SICRO, SINAPI ou "
            "contrato) no quadro de preços abaixo ou em `config/precos.csv` antes de usar o valor."
        )
    p1, p2, p3, p4, p5 = st.columns(5)
    p1.selectbox("Revestimento", orcamento.PAVIMENTOS, key="orc_pavimento",
                 index=orcamento.PAVIMENTOS.index(salvo["pavimento"]))  # fmt: skip
    p2.selectbox("Terreno de fundação", orcamento.TERRENOS, key="orc_terreno",
                 index=orcamento.TERRENOS.index(salvo["terreno"]))  # fmt: skip
    p3.number_input("BDI (%)", 0.0, 100.0, float(salvo["bdi"]), step=1.0, key="orc_bdi")
    p4.number_input("Folga de corte (m)", 0.0, 1.0, float(salvo["margem"]), step=0.05,
                    key="orc_margem", help="Vale para buracos. Revestimento perdido é orçado pela própria área.")  # fmt: skip
    p5.number_input("Recapear acima de (% do trecho)", 1.0, 100.0, float(salvo["limite"]),
                    step=1.0, key="orc_limite")  # fmt: skip

    t = orc["total"]
    k1, k2, k3, k4 = st.columns(4)
    k1.metric(
        "Plano recomendado",
        dados.reais(t["total"]),
        help="Solução escolhida por defeito, com recapeamento nos trechos críticos. Inclui BDI.",
    )
    a = orc["por_opcao"].get("A", {"total": 0.0})["total"]
    b = orc["por_opcao"].get("B", {"total": 0.0})["total"]
    k2.metric(
        "Só tapa-buraco (A)", dados.reais(a), help="Todos os defeitos com reparo superficial."
    )
    k3.metric(
        "Só remendo profundo (B)",
        dados.reais(b),
        help="Todos os defeitos com reconstrução da base.",
    )
    k4.metric(
        "Trechos a recapear (C)",
        fmt(orc["seg_c"], 0),
        help="Segmentos em que a área de corte passa do limite.",
    )
    st.caption(
        f"Plano: custo direto {dados.reais(t['direto'])} + BDI {dados.reais(t['bdi'])}. "
        "A = tapa-buraco superficial · B = remendo profundo · C = fresagem e recapeamento do trecho."
    )

    g1, g2 = st.columns(2, gap="large")
    with g1:
        st.subheader("Custo por via")
        if len(orc["por_via"]):
            fig = px.bar(orc["por_via"].head(15), x="custo", y="via", orientation="h",
                         color_discrete_sequence=[marca.OLIVA])  # fmt: skip
            fig.update_layout(
                height=max(260, 26 * min(15, len(orc["por_via"])) + 80), margin=dict(l=0, r=0, t=10, b=0),
                xaxis_title="Custo direto (R$)", yaxis_title=None, separators=",.",
                yaxis={"categoryorder": "total ascending"},
            )  # fmt: skip
            st.plotly_chart(fig, width="stretch", config=SEM_BARRA)
    with g2:
        st.subheader("Quantitativos do plano")
        st.dataframe(
            orc["por_servico"],
            hide_index=True,
            width="stretch",
            column_config={
                "servico": "Serviço",
                "unidade": "Un.",
                "quantidade": st.column_config.NumberColumn("Quantidade", format="%.2f"),
                "preco_unit": st.column_config.NumberColumn("Preço unit. (R$)", format="%.2f"),
                "custo": st.column_config.NumberColumn("Custo (R$)", format="%.2f"),
            },
        )
    with st.expander("Preços unitários (editáveis)"):
        editado = st.data_editor(
            precos_base[["servico", "unidade", "preco_unit", "fonte", "data_base"]],
            hide_index=True,
            width="stretch",
            disabled=["servico", "unidade"],
            key="orc_editor",
            column_config={
                "preco_unit": st.column_config.NumberColumn(
                    "Preço unit. (R$)", format="%.2f", min_value=0.0
                )
            },
        )
        editado = editado.assign(
            placeholder=editado["fonte"].fillna("").str.upper().isin(["PLACEHOLDER", ""])
        )
        if st.button("Aplicar preços"):
            if editado["preco_unit"].isna().any():
                st.error("Há serviço sem preço. Preencha todos antes de aplicar.")
            else:
                st.session_state["orc_precos_editados"] = editado
                st.rerun()
        st.caption(
            "Troque o preço e a fonte de cada serviço e clique em Aplicar. Vale para esta sessão; para fixar, edite `config/precos.csv`."
        )
    st.download_button(
        "Baixar orçamento (CSV)",
        orc["plano"].to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
        f"{run.name}_orcamento.csv",
        "text/csv",
    )

# ============================================================== dados e relatorio
if aba == "Dados e relatório":
    st.subheader("Relatório executivo")
    r1, r2 = st.columns([1, 3])
    # assinatura do que esta na tela: muda com filtro, revisao ou orcamento
    assinatura = (
        run.name, len(det), round(float(det["area_m2"].sum()), 3) if len(det) else 0.0,
        len(seg_f), round(orc["total"]["total"], 2), _carimbo_rev(run),
    )  # fmt: skip
    if r1.button("Gerar relatório", type="primary", width="stretch"):
        st.session_state["relatorio"] = (
            assinatura,
            relatorio.gerar_html(run, det, seg_f, resumo, params, orc).encode("utf-8"),
        )
    pronto = st.session_state.get("relatorio")
    if pronto and pronto[0] == assinatura:
        r2.download_button(
            "Baixar relatório (HTML, pronto para imprimir em PDF)",
            pronto[1],
            f"{run.name}_relatorio.html",
            "text/html",
        )
    else:
        r2.caption(
            "Uma página com indicadores, mapa, condição por via, trechos prioritários, estimativa de reparo e os maiores defeitos, com os filtros atuais."
        )
    st.subheader("Defeitos")
    tab = dados.tabela(det)
    st.dataframe(
        tab,
        hide_index=True,
        width="stretch",
        height=340,
        column_config={
            "id": st.column_config.NumberColumn("Id", format="%d"),
            "classe": "Tipo",
            "severidade": "Severidade",
            "area_m2": st.column_config.NumberColumn("Área (m²)", format="%.2f"),
            "largura_m": st.column_config.NumberColumn("Largura (m)", format="%.2f"),
            "comprimento_m": st.column_config.NumberColumn("Comprimento (m)", format="%.2f"),
            "confianca": st.column_config.ProgressColumn(
                "Confiança", format="%.2f", min_value=0.0, max_value=1.0
            ),
            "via": "Via",
            "segmento": st.column_config.NumberColumn("Segmento", format="%d"),
            "na_emenda": "Na emenda",
            "lat": st.column_config.NumberColumn("Latitude", format="%.6f"),
            "lon": st.column_config.NumberColumn("Longitude", format="%.6f"),
            "x": st.column_config.NumberColumn("E (m)", format="%.1f"),
            "y": st.column_config.NumberColumn("N (m)", format="%.1f"),
        },
    )
    b1, b2, b3, _ = st.columns([1, 1, 1, 3])
    b1.download_button(
        "Baixar CSV", dados.csv_bytes(det), f"{run.name}_defeitos.csv", "text/csv", width="stretch"
    )
    b2.download_button(
        "Baixar XLSX",
        dados.xlsx_bytes(det, seg_f),
        f"{run.name}_defeitos.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )
    b3.download_button(
        "Baixar GeoJSON",
        dados.geojson_bytes(det),
        f"{run.name}_defeitos.geojson",
        "application/geo+json",
        width="stretch",
    )

# ============================================================== nova analise
if aba == "Nova análise":
    _nova_analise()

# ============================================================== proximos passos
if aba == "Próximos passos":
    st.subheader("Do detector de hoje ao modelo treinado")
    st.markdown(
        "O detector atual **não usa aprendizado**: compara cada ponto com o pavimento em volta. "
        "Isso basta para localizar e medir, mas não distingue buraco de remendo, mancha ou trinca. "
        "O próximo passo é plugar um modelo treinado (o YOLO do mestrado) pela mesma interface, sem "
        "mexer no resto do sistema: entrada, medidas, condição por trecho, orçamento e relatório ficam iguais."
    )
    pasta_base = acervo.pasta_imagens() / "uavpdd"
    base = base_publica.resumo(pasta_base)
    if base:
        b1, b2 = st.columns([1, 1.4], gap="large")
        with b1:
            st.markdown(
                f"**Base pública para o treino: UAV-PDD2023.** {fmt(base['imagens'], 0)} fotos de drone "
                f"com {fmt(base['defeitos'], 0)} defeitos anotados em seis classes "
                "([Zenodo](https://zenodo.org/records/8429208), Yan e Zhang, 2023)."
            )
            tabela_base = pd.DataFrame(
                {
                    "Classe": [base_publica.ROTULO.get(k, k) for k in base["por_classe"]],
                    "Defeitos anotados": list(base["por_classe"].values()),
                }
            ).sort_values("Defeitos anotados")
            fig = px.bar(tabela_base, x="Defeitos anotados", y="Classe", orientation="h",
                         color_discrete_sequence=[marca.OLIVA], text="Defeitos anotados")  # fmt: skip
            fig.update_layout(
                height=300, margin=dict(l=0, r=0, t=10, b=0), yaxis_title=None, separators=",."
            )
            st.plotly_chart(fig, width="stretch", config=SEM_BARRA)
            st.caption(
                "A base é quase toda de trincas; panelas são poucas. Para o nosso caso ela precisa ser "
                "completada com imagens anotadas das nossas próprias vias."
            )
        with b2:
            fotos = base_publica.exemplos(pasta_base, 4)
            for linha_fotos in (fotos[:2], fotos[2:4]):
                for coluna, foto in zip(st.columns(2), linha_fotos, strict=False):
                    coluna.image(str(foto), width="stretch")
            if fotos:
                st.caption(
                    "Exemplos da base, com as anotações originais: é este tipo de rótulo que o modelo aprende."
                )
    else:
        st.info(
            "Coloque a base UAV-PDD2023 em `dados/uavpdd` (pastas Annotations e JPEGImages) para ver os exemplos aqui."
        )

# ============================================================== rodape
st.divider()
credito = ""
if fonte:
    credito = (
        f" · Imagem: {fonte.get('produtor') or 'autor não informado'}, "
        f"{fonte.get('licenca') or ''}, via {fonte.get('origem') or 'OpenAerialMap'}"
    )
st.caption(
    f"Detector {resumo['detector']} v{resumo['versao']} · "
    f"GSD {fmt(100 * resumo['gsd_m'], 1)} cm/px · SRC {resumo.get('src', '')}{credito}"
)
with st.expander("Limites do método"):
    if resumo.get("limites_detector"):
        st.markdown(resumo["limites_detector"])
    st.markdown(
        "As faixas de severidade são de trabalho (por área), não de norma. Os números são "
        "candidatos a defeito; confirme pelos recortes antes de usar."
    )
