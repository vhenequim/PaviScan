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

from paviscan import acervo, base_publica, orcamento, relatorio
from paviscan.painel import dados
from paviscan.painel.dados import CONCEITOS, ROTULO_CLASSE, ROTULO_SEVERIDADE, SEVERIDADES, fmt

COR = {"baixa": "#E3B23C", "media": "#EE7B30", "alta": "#D62839"}
COR_RGB = {k: [int(v[i : i + 2], 16) for i in (1, 3, 5)] for k, v in COR.items()}
COR_CONCEITO = relatorio.COR_CONCEITO
COR_CONCEITO_RGB = {k: [int(v[i : i + 2], 16) for i in (1, 3, 5)] for k, v in COR_CONCEITO.items()}
VERDE = "#1F3A2B"
RUNS = Path(os.environ.get("PAVISCAN_RUNS", "runs"))
ALTURA_MAPA = 620
SEM_BARRA = {"displayModeBar": False}

st.set_page_config(page_title="PaviScan", page_icon="🛣️", layout="wide")
st.markdown(
    f"""
    <style>
      .block-container {{padding-top: 3.2rem; padding-bottom: 2rem; max-width: 1500px;}}
      [data-testid="stMetric"] {{background: #F3F5F2; border-radius: 12px; padding: 14px 16px;
                                 border-left: 5px solid {VERDE};}}
      [data-testid="stMetricValue"] {{font-size: 1.8rem;}}
      .faixa {{background: linear-gradient(100deg, {VERDE} 0%, #2F5D43 70%, #3E7A57 100%);
               color: #fff; border-radius: 14px; padding: 20px 26px; margin-bottom: 14px;}}
      .faixa h1 {{color: #fff; margin: 0; font-size: 2.1rem; padding: 0;}}
      .faixa p {{margin: 6px 0 0; opacity: .88; font-size: .92rem;}}
      .selo {{display: inline-block; color: #fff; font-weight: 700; border-radius: 7px;
              padding: 2px 12px;}}
      .cartao {{background: #F3F5F2; border-radius: 12px; padding: 12px 14px; height: 100%;}}
      .cartao small {{color: #55615A;}}
      .passo {{font-weight: 700; color: {VERDE}; font-size: 1.05rem; margin: 6px 0 2px;}}
    </style>
    """,
    unsafe_allow_html=True,
)


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
def _tabelas_orcamento():
    return orcamento.carregar_tabelas()


def _carimbo(run: Path) -> float:
    return (run / "resumo.json").stat().st_mtime


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
    estilo = {"backgroundColor": VERDE, "color": "white", "fontSize": "13px"}
    return pdk.Deck(
        layers=camadas,
        initial_view_state=vista,
        map_style="light",
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
    recorte = run / "recortes" / f"{int(linha.id)}.jpg"
    if recorte.is_file():
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
        "seg_c": int(orc.loc[orc["opcao"] == "C", "id"].nunique()),
    }


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
        if meta["gsd_cm"] > 5:
            st.warning(
                "GSD acima de 5 cm/px: buracos pequenos (30 cm) ficam abaixo do que o método enxerga."
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
        ["asfalto", "terra"],
        format_func={
            "asfalto": "Asfalto em bom estado de cor (cinza)",
            "terra": "Via muito gasta ou de terra",
        }.get,
        help="Define como a borda da via é achada em volta do eixo.",
        disabled=modo in ("Usar a pista já guardada", "Enviar arquivo"),
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
    st.session_state["levantamento"] = nome_run
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
        "<div class='faixa'><h1>PaviScan</h1><p>Envie uma ortofoto, indique a via e veja os defeitos "
        "medidos, a condição por trecho e a estimativa de reparo.</p></div>",
        unsafe_allow_html=True,
    )
    _nova_analise()
    st.stop()

with st.sidebar:
    nomes = [r.name for r in runs]
    if st.session_state.get("levantamento") not in nomes:
        st.session_state["levantamento"] = nomes[0]
    if len(runs) > 1:
        st.selectbox("Levantamento", nomes, key="levantamento")
    run = runs[nomes.index(st.session_state["levantamento"])]
    det_todos, seg, resumo = _carregar(str(run), _carimbo(run))

    st.markdown("### Filtros")
    pre = f"{run.name}:"
    chaves = [pre + k for k in ("classe", "area", "sev", "conf", "vias", "segs")]
    area_min = area_max = conf_min = sev = vias = segs = classes = None
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
        st.button("Limpar filtros", on_click=_limpar, args=(chaves,), width="stretch")
    else:
        st.caption("Nenhum defeito detectado neste levantamento.")

params = _params()
det = dados.filtrar(det_todos, area_min, area_max, conf_min, sev, segs, vias, classes)
seg_f = dados.filtrar_segmentos(seg, segs, vias)  # recorte de via/segmento escolhido
filtrado = len(det) < len(det_todos)
ind = dados.resumir(det, seg_f)
cond = dados.condicao_geral(det, seg_f, params["condicao"])
cond_seg = dados.condicao_segmentos(det, seg_f, params["condicao"])
tab_vias = dados.por_via(det, seg_f, params["condicao"])

# orcamento do recorte, com os parametros escolhidos na aba Orcamento (ou os do params.yaml)
solucoes, precos_base = _tabelas_orcamento()
cfg_orc = dict(params["orcamento"])
cfg_orc["pavimento"] = st.session_state.get("orc_pavimento", cfg_orc["pavimento"])
cfg_orc["terreno"] = st.session_state.get("orc_terreno", cfg_orc["terreno"])
cfg_orc["bdi"] = st.session_state.get("orc_bdi", 100 * cfg_orc["bdi"]) / 100
cfg_orc["margem_corte_m"] = st.session_state.get("orc_margem", cfg_orc["margem_corte_m"])
cfg_orc["limite_recapeamento"] = (
    st.session_state.get("orc_limite", 100 * cfg_orc["limite_recapeamento"]) / 100
)
precos = st.session_state.get("orc_precos_editados", precos_base)
orc = _orcar(det, seg_f, cfg_orc, precos, solucoes)

# ============================================================== cabecalho
fonte = resumo.get("fonte") or {}
titulo = fonte.get("titulo") or run.name.replace("_", " ").title()
st.markdown(
    f"<div class='faixa'><h1>{html.escape(titulo)}</h1><p>Levantamento <b>{html.escape(run.name)}</b> · "
    f"GSD {fmt(100 * resumo['gsd_m'], 1)} cm/px · {fmt(float(seg['area_pista_m2'].sum()), 0)} m² de pista em "
    f"{len(seg)} segmentos de {fmt(float(seg['comprimento_m'].sum()), 0)} m"
    + (f" · imagem de {html.escape(str(fonte['data']))}" if fonte.get("data") else "")
    + "</p></div>",
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
    st.caption(f"Filtro ativo: **{len(det)} de {len(det_todos)} defeitos**.")
if resumo.get("cortadas_por_janela"):
    st.warning(
        f"{resumo['cortadas_por_janela']} detecção(ões) grandes ficaram na emenda das janelas de "
        "processamento e podem estar truncadas (coluna `na_emenda` da tabela)."
    )
if len(det_todos) and det.empty:
    st.warning("Nenhum defeito passa nos filtros atuais. Afrouxe um filtro ou limpe todos.")

aba_geral, aba_vias, aba_inspecao, aba_orc, aba_dados, aba_nova, aba_passos = st.tabs(
    [
        "Visão geral", "Vias e trechos", "Inspeção", "Orçamento", "Dados e relatório",
        "Nova análise", "Próximos passos",
    ]
)  # fmt: skip

# ============================================================== visao geral
with aba_geral:
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
                recorte = (
                    run / "recortes" / f"{int(do_trecho['id'].iloc[0])}.jpg"
                    if len(do_trecho)
                    else None
                )
                if recorte is not None and recorte.is_file():
                    st.image(str(recorte), width="stretch")
                st.markdown(
                    f"<div class='cartao'>{_selo(trecho.conceito)} <b>Segmento {int(trecho.segmento)}</b><br/>"
                    f"<small>{html.escape(str(trecho.via))}</small><br/>{int(trecho.n_buracos)} defeito(s) · "
                    f"{fmt(trecho.area_buracos_m2, 1)} m² · {fmt(trecho.pct_afetada, 1)} % do trecho</div>",
                    unsafe_allow_html=True,
                )

# ============================================================== vias e trechos
with aba_vias:
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

# ============================================================== inspecao
with aba_inspecao:
    st.caption(
        "Recorte da ortofoto em resolução cheia para cada defeito, com o contorno detectado. "
        "É aqui que se separa defeito de sombra, mancha e veículo."
    )
    if len(det):
        k1, k2, _ = st.columns([1.2, 1, 3])
        criterio = k1.selectbox("Ordenar por", ["Maior área", "Maior confiança", "Menor confiança"])
        quantos = k2.select_slider("Quantidade", [6, 12, 18, 24, 36], value=12)
        coluna, crescente = {
            "Maior área": ("area_m2", False),
            "Maior confiança": ("confianca", False),
            "Menor confiança": ("confianca", True),
        }[criterio]
        amostra = det.sort_values(coluna, ascending=crescente).head(quantos)
        grade = st.columns(6)
        for i, item in enumerate(amostra.itertuples()):
            recorte = run / "recortes" / f"{int(item.id)}.jpg"
            with grade[i % 6]:
                if recorte.is_file():
                    st.image(str(recorte), width="stretch")
                st.caption(
                    f"**#{int(item.id)}** · {fmt(item.area_m2)} m² · conf. {fmt(item.confianca)}  \n"
                    f"{ROTULO_CLASSE.get(item.classe, item.classe)} · {item.via}"
                )

# ============================================================== orcamento
with aba_orc:
    if orc["placeholder"]:
        st.warning(
            "Os preços unitários são **ilustrativos**. Substitua pelos de referência (SICRO, SINAPI ou "
            "contrato) no quadro de preços abaixo ou em `config/precos.csv` antes de usar o valor."
        )
    p1, p2, p3, p4, p5 = st.columns(5)
    p1.selectbox("Revestimento", orcamento.PAVIMENTOS, key="orc_pavimento",
                 index=orcamento.PAVIMENTOS.index(params["orcamento"]["pavimento"]))  # fmt: skip
    p2.selectbox("Terreno de fundação", orcamento.TERRENOS, key="orc_terreno",
                 index=orcamento.TERRENOS.index(params["orcamento"]["terreno"]))  # fmt: skip
    p3.number_input(
        "BDI (%)", 0.0, 100.0, 100 * params["orcamento"]["bdi"], step=1.0, key="orc_bdi"
    )
    p4.number_input("Folga de corte (m)", 0.0, 1.0, float(params["orcamento"]["margem_corte_m"]),
                    step=0.05, key="orc_margem")  # fmt: skip
    p5.number_input("Recapear acima de (% do trecho)", 1.0, 100.0,
                    100 * params["orcamento"]["limite_recapeamento"], step=1.0, key="orc_limite")  # fmt: skip

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
                         color_discrete_sequence=["#2F5D43"])  # fmt: skip
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
with aba_dados:
    st.subheader("Relatório executivo")
    r1, r2 = st.columns([1, 3])
    if r1.button("Gerar relatório", type="primary", width="stretch"):
        st.session_state["relatorio"] = (
            run.name,
            relatorio.gerar_html(run, det, seg_f, resumo, params, orc).encode("utf-8"),
        )
    pronto = st.session_state.get("relatorio")
    if pronto and pronto[0] == run.name:
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
with aba_nova:
    _nova_analise()

# ============================================================== proximos passos
with aba_passos:
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
                         color_discrete_sequence=["#2F5D43"], text="Defeitos anotados")  # fmt: skip
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
