"""Dashboard do PaviScan. So interface: toda conta vem de paviscan.painel.dados."""

from __future__ import annotations

import html
import math
import os
import re
from pathlib import Path

import pandas as pd
import plotly.express as px
import pydeck as pdk
import streamlit as st

from paviscan.painel import dados
from paviscan.painel.dados import ROTULO_SEVERIDADE, SEVERIDADES, fmt

COR = {"baixa": "#E3B23C", "media": "#EE7B30", "alta": "#D62839"}
COR_RGB = {k: [int(v[i : i + 2], 16) for i in (1, 3, 5)] for k, v in COR.items()}
VERDE = "#2F6B3F"
RUNS = Path(os.environ.get("PAVISCAN_RUNS", "runs"))
ALTURA_MAPA = 600

st.set_page_config(page_title="PaviScan", page_icon="🛣️", layout="wide")
st.markdown(
    """
    <style>
      .block-container {padding-top: 2.2rem; padding-bottom: 2rem;}
      [data-testid="stMetric"] {background: #F3F5F2; border-radius: 10px; padding: 14px 16px;}
      [data-testid="stMetricValue"] {font-size: 1.9rem;}
      h1 {margin-bottom: 0;}
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


def _carimbo(run: Path) -> float:
    return (run / "resumo.json").stat().st_mtime


def _limpar(chaves: list[str]) -> None:
    for k in chaves:
        st.session_state.pop(k, None)


def _processar_nova() -> None:
    """Bloco 'Processar nova imagem': caminho de arquivo, nao upload (ortofoto e grande)."""
    with st.expander("Processar nova imagem"):
        orto = st.text_input("Ortofoto (GeoTIFF)", placeholder="dados/minha_area/orto.tif")
        pista = st.text_input(
            "Polígono da pista (GeoJSON)",
            placeholder="vazio = vias do OpenStreetMap",
            help="Sem polígono, a pista é gerada pelos eixos de via do OpenStreetMap.",
        )
        nome = st.text_input("Nome do levantamento", placeholder="minha_area")
        if not st.button("Processar", type="primary", width="stretch"):
            return
        if not orto or not Path(orto).is_file():
            st.error("Ortofoto não encontrada.")
            return
        if pista and not Path(pista).is_file():
            st.error("Polígono da pista não encontrado.")
            return
        from paviscan import pipeline, pista_osm

        bruto = nome.strip() or Path(orto).parent.name or "levantamento"
        destino = RUNS / (re.sub(r"[^A-Za-z0-9_-]+", "_", bruto).strip("_") or "levantamento")
        barra = st.progress(0.0, text="Preparando")
        try:
            caminho_pista = Path(pista) if pista else Path(orto).parent / "pista.geojson"
            if not caminho_pista.is_file():
                barra.progress(0.02, text="Gerando a pista pelo OpenStreetMap")
                pista_osm.gerar(Path(orto), caminho_pista)
            pipeline.processar(
                Path(orto),
                caminho_pista,
                destino,
                progresso=lambda f, t: barra.progress(min(1.0, f), text=t),
            )
        except Exception as erro:  # mostra o motivo na tela em vez de derrubar o painel
            st.error(f"Não foi possível processar: {erro}")
            with st.expander("Detalhe técnico"):
                st.exception(erro)
            return
        st.session_state["levantamento"] = destino.name
        st.cache_data.clear()
        st.rerun()


def _zoom(lon0: float, lat0: float, lon1: float, lat1: float, largura_px: int) -> float:
    """Zoom que enquadra o retangulo num mapa de `largura_px` x ALTURA_MAPA."""
    por_lon = math.log2(largura_px * 360 / (512 * max(lon1 - lon0, 1e-7)))
    cos_lat = math.cos(math.radians((lat0 + lat1) / 2))
    por_lat = math.log2(ALTURA_MAPA * 360 * cos_lat / (512 * max(lat1 - lat0, 1e-7)))
    return max(1.0, min(por_lon, por_lat) - 0.15)


def _dica_buraco(linha) -> str:
    return (
        f"<b>Buraco {int(linha.id)}</b> · {ROTULO_SEVERIDADE[linha.severidade]}<br/>"
        f"Área: {fmt(linha.area_m2)} m²<br/>"
        f"{fmt(linha.largura_m)} × {fmt(linha.comprimento_m)} m<br/>"
        f"Confiança: {fmt(linha.confianca)}<br/>"
        f"{html.escape(str(linha.via))} · segmento {int(linha.segmento)}"
    )


def _mapa(det, seg, resumo, imagem, mostrar_seg: bool, mostrar_orto: bool):
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
    if mostrar_seg:
        s = dados.segmentos_filtrados(det, seg)
        teto = max(float(s["pct_afetada"].max()), 1e-9)
        s["cor"] = [
            [214, 40, 57, int(25 + 190 * min(1.0, v / teto))] if v > 0 else [47, 107, 63, 40]
            for v in s["pct_afetada"]
        ]
        s["dica"] = [
            f"<b>Segmento {int(i)}</b> · {html.escape(str(via))}<br/>{int(n)} buraco(s)<br/>"
            f"{fmt(a)} m² afetados ({fmt(p)}% do segmento)"
            for i, via, n, a, p in zip(
                s["segmento"], s["via"], s["n_buracos"], s["area_buracos_m2"], s["pct_afetada"],
                strict=True,
            )
        ]  # fmt: skip
        camadas.append(
            pdk.Layer(
                "GeoJsonLayer",
                id="segmentos",
                data=dados.para_mapa(s, ["segmento", "cor", "dica"]),
                get_fill_color="properties.cor",
                get_line_color=[255, 255, 255, 170],
                get_line_width=0.05,  # metros; o minimo em pixel vale com o mapa afastado
                line_width_min_pixels=1,
                pickable=True,
            )
        )
    if len(det):
        d = det.copy()
        d["cor"] = [[*COR_RGB[s], 110] for s in d["severidade"]]
        d["cor_linha"] = [[*COR_RGB[s], 255] for s in d["severidade"]]
        d["dica"] = [_dica_buraco(linha) for linha in d.itertuples()]
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
        # anel em volta de cada buraco: aparece mesmo com o mapa afastado
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
    )
    estilo = {"backgroundColor": "#1F2A22", "color": "white", "fontSize": "13px"}
    return pdk.Deck(
        layers=camadas,
        initial_view_state=vista,
        map_style="light",
        tooltip={"html": "{dica}", "style": estilo},
    )


def _selecionado(evento) -> int | None:
    """Id do buraco clicado no mapa, se houver."""
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
        f"**Buraco {int(linha.id)}** · "
        f"<span style='color:{cor};font-weight:600'>{ROTULO_SEVERIDADE[linha.severidade]}</span>",
        unsafe_allow_html=True,
    )
    st.caption(
        f"{fmt(linha.area_m2)} m² · {fmt(linha.largura_m)} × {fmt(linha.comprimento_m)} m · "
        f"confiança {fmt(linha.confianca)}  \n{linha.via} · segmento {int(linha.segmento)}"
    )


# ---------------------------------------------------------------- barra lateral
with st.sidebar:
    st.markdown("## 🛣️ PaviScan")
    runs = dados.listar_runs(RUNS)
    if not runs:
        _processar_nova()
        st.info(
            f"Nenhum levantamento em `{RUNS}`.\n\nProcesse uma imagem com\n"
            "`paviscan run --orto X.tif --saida runs/nome`\nou use o bloco acima."
        )
        st.stop()
    nomes = [r.name for r in runs]
    if st.session_state.get("levantamento") not in nomes:
        st.session_state["levantamento"] = nomes[0]
    if len(runs) > 1:
        st.selectbox("Levantamento", nomes, key="levantamento")
    run = runs[nomes.index(st.session_state["levantamento"])]
    det_todos, seg, resumo = _carregar(str(run), _carimbo(run))

    st.markdown("### Filtros")
    pre = f"{run.name}:"
    chaves = [pre + k for k in ("area", "sev", "conf", "vias", "segs")]
    area_min = area_max = conf_min = sev = vias = segs = None
    if len(det_todos):
        a0 = math.floor(float(det_todos["area_m2"].min()) * 100) / 100
        a1 = math.ceil(float(det_todos["area_m2"].max()) * 100) / 100
        if a1 > a0:
            lo, hi = st.slider("Área (m²)", a0, a1, (a0, a1), step=0.01, key=chaves[0])
            area_min = lo if lo > a0 else None
            area_max = hi if hi < a1 else None
        presentes = [s for s in SEVERIDADES if s in set(det_todos["severidade"])]
        escolha = st.multiselect(
            "Severidade", presentes, default=presentes, format_func=ROTULO_SEVERIDADE.get,
            key=chaves[1],
        )  # fmt: skip
        sev = escolha if len(escolha) < len(presentes) else None
        conf = st.slider("Confiança mínima", 0.0, 1.0, 0.0, step=0.05, key=chaves[2])
        conf_min = conf if conf > 0 else None
        nomes_vias = sorted(seg["via"].dropna().unique())
        if len(nomes_vias) > 1:
            vias = st.multiselect("Vias", nomes_vias, placeholder="Todas", key=chaves[3]) or None
        opcoes = sorted(int(s) for s in seg["segmento"])
        if vias:
            opcoes = sorted(int(s) for s in seg.loc[seg["via"].isin(vias), "segmento"])
        segs = st.multiselect("Segmentos", opcoes, placeholder="Todos", key=chaves[4]) or None
        st.button("Limpar filtros", on_click=_limpar, args=(chaves,), width="stretch")
    else:
        st.caption("Nenhum buraco detectado neste levantamento.")
    st.divider()
    _processar_nova()

det = dados.filtrar(det_todos, area_min, area_max, conf_min, sev, segs, vias)
filtrado = len(det) < len(det_todos)
seg_f = dados.filtrar_segmentos(seg, segs, vias)  # recorte de via/segmento escolhido
ind = dados.resumir(det, seg_f)

# ---------------------------------------------------------------- cabecalho e indicadores
fonte = resumo.get("fonte") or {}
titulo = fonte.get("titulo") or run.name
st.title(f"{titulo}")
st.caption(
    f"Levantamento `{run.name}` · ortofoto {resumo.get('ortofoto', '')} · "
    f"GSD {fmt(100 * resumo['gsd_m'], 1)} cm/px · "
    f"{fmt(resumo['area_pista_m2'], 0)} m² de pista em {len(seg)} segmentos"
    + (f" · imagem de {fonte['data']}" if fonte.get("data") else "")
)

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Buracos", fmt(ind["n_buracos"], 0))
c2.metric("Área total", f"{fmt(ind['area_total_m2'])} m²")
c3.metric("Pista afetada", f"{fmt(ind['pct_afetada'], 2)} %")
c4.metric("Buracos por 100 m", fmt(ind["buracos_por_100m"], 1))
c5.metric("Maior buraco", f"{fmt(ind['maior_buraco_m2'])} m²")
if filtrado:
    st.caption(f"Filtro ativo: **{len(det)} de {len(det_todos)} buracos**.")
if resumo.get("cortadas_por_janela"):
    st.warning(
        f"{resumo['cortadas_por_janela']} detecção(ões) grandes ficaram na emenda das janelas de "
        "processamento e podem estar truncadas (coluna `na_emenda` da tabela)."
    )
if len(det_todos) and det.empty:
    st.warning("Nenhum buraco passa nos filtros atuais. Afrouxe um filtro ou limpe todos.")

# ---------------------------------------------------------------- mapa
st.subheader("Mapa")
col_mapa, col_lado = st.columns([3.2, 1], gap="medium")
with col_mapa:
    o1, o2, _ = st.columns([1, 1.4, 2])
    mostrar_orto = o1.toggle("Ortofoto", value=True)
    mostrar_seg = o2.toggle("Segmentos por % afetada", value=False)
    baralho = _mapa(det, seg_f, resumo, _imagem(str(run), _carimbo(run)), mostrar_seg, mostrar_orto)
    evento = st.pydeck_chart(
        baralho,
        height=ALTURA_MAPA,
        on_select="rerun",
        selection_mode="single-object",
        key=f"mapa:{run.name}",
    )
with col_lado:
    escolhido = _selecionado(evento)
    linha = det[det["id"] == escolhido] if escolhido is not None else det.iloc[0:0]
    if len(linha):
        _ficha(next(linha.itertuples()), run)
    else:
        st.caption("Clique em um buraco no mapa para ver o recorte em resolução cheia.")
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
            height=230, margin=dict(l=0, r=0, t=30, b=0), showlegend=False,
            title_text="Por severidade", title_font_size=14, xaxis_title=None, yaxis_title=None,
            yaxis_visible=False,
        )  # fmt: skip
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

# ---------------------------------------------------------------- graficos
if len(det):
    g1, g2 = st.columns(2, gap="medium")
    with g1:
        st.subheader("Distribuição das áreas")
        d = det.assign(Severidade=det["severidade"].map(ROTULO_SEVERIDADE))
        fig = px.histogram(
            d,
            x="area_m2",
            color="Severidade",
            nbins=30,
            color_discrete_map={ROTULO_SEVERIDADE[k]: v for k, v in COR.items()},
            category_orders={"Severidade": [ROTULO_SEVERIDADE[s] for s in SEVERIDADES]},
        )
        fig.update_layout(
            height=320, margin=dict(l=0, r=0, t=10, b=0), separators=",.", bargap=0.05,
            xaxis_title="Área do buraco (m²)", yaxis_title="Buracos", legend_title=None,
            legend=dict(orientation="h", y=1.1),
        )  # fmt: skip
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    with g2:
        st.subheader("Área afetada ao longo da pista")
        s = dados.por_segmento(det, seg_f)
        fig = px.bar(
            s,
            x="segmento",
            y="pct_afetada",
            hover_data={"via": True, "n_buracos": True, "area_buracos_m2": ":.2f"},
            color_discrete_sequence=[VERDE],
        )
        fig.update_layout(
            height=320, margin=dict(l=0, r=0, t=10, b=0), separators=",.",
            xaxis_title="Segmento (ordem da pista)", yaxis_title="% do segmento afetada",
        )  # fmt: skip
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    # ------------------------------------------------------------ ranking
    st.subheader("Piores segmentos")
    pior = dados.ranking(det, seg_f, 10)
    st.dataframe(
        pior[["segmento", "via", "n_buracos", "area_buracos_m2", "pct_afetada"]],
        hide_index=True,
        width="stretch",
        column_config={
            "segmento": st.column_config.NumberColumn("Segmento", format="%d"),
            "via": "Via",
            "n_buracos": st.column_config.NumberColumn("Buracos", format="%d"),
            "area_buracos_m2": st.column_config.NumberColumn("Área afetada (m²)", format="%.2f"),
            "pct_afetada": st.column_config.ProgressColumn(
                "% afetada",
                format="%.2f%%",
                min_value=0.0,
                max_value=max(float(pior["pct_afetada"].max()), 0.01) if len(pior) else 1.0,
            ),
        },
    )

    # ------------------------------------------------------------ conferencia visual
    st.subheader("Conferência visual")
    st.caption(
        "Recorte da ortofoto em resolução cheia para cada buraco, com o contorno detectado. "
        "É aqui que se separa buraco de sombra, mancha e veículo."
    )
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
                f"{ROTULO_SEVERIDADE[item.severidade]} · {item.via}"
            )

    # ------------------------------------------------------------ tabela e downloads
    st.subheader("Buracos")
    tab = dados.tabela(det)
    st.dataframe(
        tab,
        hide_index=True,
        width="stretch",
        height=320,
        column_config={
            "id": st.column_config.NumberColumn("Id", format="%d"),
            "severidade": "Severidade",
            "area_m2": st.column_config.NumberColumn("Área (m²)", format="%.2f"),
            "largura_m": st.column_config.NumberColumn("Largura (m)", format="%.2f"),
            "comprimento_m": st.column_config.NumberColumn("Comprimento (m)", format="%.2f"),
            "confianca": st.column_config.ProgressColumn(
                "Confiança", format="%.2f", min_value=0.0, max_value=1.0
            ),
            "via": "Via",
            "segmento": st.column_config.NumberColumn("Segmento", format="%d"),
            "lat": st.column_config.NumberColumn("Latitude", format="%.6f"),
            "lon": st.column_config.NumberColumn("Longitude", format="%.6f"),
            "x": st.column_config.NumberColumn("E (m)", format="%.1f"),
            "y": st.column_config.NumberColumn("N (m)", format="%.1f"),
        },
    )
    b1, b2, b3, _ = st.columns([1, 1, 1, 3])
    b1.download_button(
        "Baixar CSV", dados.csv_bytes(det), f"{run.name}_buracos.csv", "text/csv",
        width="stretch",
    )  # fmt: skip
    b2.download_button(
        "Baixar XLSX",
        dados.xlsx_bytes(det, seg_f),
        f"{run.name}_buracos.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        width="stretch",
    )
    b3.download_button(
        "Baixar GeoJSON", dados.geojson_bytes(det), f"{run.name}_buracos.geojson",
        "application/geo+json", width="stretch",
    )  # fmt: skip

# ---------------------------------------------------------------- rodape
st.divider()
credito = ""
if fonte:
    credito = (
        f" · Imagem: {fonte.get('produtor') or 'autor não informado'}, "
        f"{fonte.get('licenca') or ''}, via {fonte.get('origem') or 'OpenAerialMap'}"
    )
st.caption(
    f"Detector {resumo['detector']} v{resumo['versao']} · "
    f"GSD {fmt(100 * resumo['gsd_m'], 1)} cm/px · SRC {resumo.get('src', '')}"
    f"{credito} · Vias © colaboradores do OpenStreetMap"
)
with st.expander("Limites do método"):
    if resumo.get("limites_detector"):
        st.markdown(resumo["limites_detector"])
    st.markdown(
        "As faixas de severidade são de trabalho (por área), não de norma. Os números são "
        "candidatos a buraco; confirme pelos recortes antes de usar."
    )
