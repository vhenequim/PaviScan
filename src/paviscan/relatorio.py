"""Relatorio executivo de um levantamento: uma pagina HTML autocontida, pronta para imprimir."""

from __future__ import annotations

import base64
import html
from pathlib import Path

import cv2
import geopandas as gpd
import numpy as np
import pandas as pd

from . import geometria, marca
from .painel import dados

COR_SEVERIDADE = {"baixa": (227, 178, 60), "media": (238, 123, 48), "alta": (214, 40, 57)}
COR_CONCEITO = {"A": "#2E7D32", "B": "#7CB342", "C": "#F9A825", "D": "#EF6C00", "E": "#C62828"}


def mapa_estatico(
    run: Path,
    defeitos: gpd.GeoDataFrame,
    resumo: dict,
    lado_px: int = 1500,
    segmentos: gpd.GeoDataFrame | None = None,
) -> bytes:
    """JPEG da ortofoto com a caixa de cada defeito (retangulo paralelo a via), pela severidade.

    Com `segmentos`, a imagem e recortada na area deles e o contorno de cada um aparece em
    branco. Caixa pequena demais para enxergar na escala do mapa e aumentada ate ~10 px.
    """
    arq = dados.arquivo_previa(run)
    img = cv2.imread(str(arq), cv2.IMREAD_UNCHANGED) if arq is not None else None
    if img is None:
        return b""
    if img.shape[2] == 4:  # fundo branco onde nao ha dado
        alfa = img[..., 3:4].astype(np.float32) / 255.0
        img = (img[..., :3].astype(np.float32) * alfa + 255.0 * (1 - alfa)).astype(np.uint8)
    (lat_s, lon_o), (lat_n, lon_l) = resumo["previa_limites"]
    h0, w0 = img.shape[:2]

    def na_previa(lon, lat):
        return (
            (np.asarray(lon) - lon_o) / (lon_l - lon_o) * w0,
            (lat_n - np.asarray(lat)) / (lat_n - lat_s) * h0,
        )

    x0, y0 = 0, 0
    tem_seg = segmentos is not None and len(segmentos)
    if tem_seg:  # recorta na area escolhida, com folga
        oeste, sul, leste, norte = segmentos.to_crs(4326).total_bounds
        (xa, xb), (yb, ya) = na_previa([oeste, leste], [sul, norte])
        folga = 0.05 * max(xb - xa, yb - ya) + 15
        x0, y0 = max(0, int(xa - folga)), max(0, int(ya - folga))
        x1, y1 = min(w0, int(xb + folga) + 1), min(h0, int(yb + folga) + 1)
        if x1 - x0 >= 8 and y1 - y0 >= 8:
            img = img[y0:y1, x0:x1]
        else:
            x0, y0 = 0, 0
    h, w = img.shape[:2]
    f = min(4.0, lado_px / max(h, w))  # area pequena: amplia (ate 4x) para caber na pagina
    img = cv2.resize(
        img, (max(1, int(w * f)), max(1, int(h * f))),
        interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_CUBIC,
    )  # fmt: skip

    def para_px(geom) -> np.ndarray:
        xy = np.asarray(geom.exterior.coords)
        px, py = na_previa(xy[:, 0], xy[:, 1])
        return np.stack([(px - x0) * f, (py - y0) * f], axis=1)

    if tem_seg:  # segmento pode ser GeometryCollection com sobra de linha: so os poligonos
        for g in segmentos.to_crs(4326).geometry:
            for p in geometria.poligonos(g):
                pts = para_px(p).round().astype(np.int32)
                cv2.polylines(img, [pts], True, (245, 245, 245), 1, cv2.LINE_AA)
    if len(defeitos):
        d = defeitos.sort_values("area_m2")  # os maiores por cima
        rets = geometria.caixas_de(d).to_crs(4326)
        for ret, sev in zip(rets, d["severidade"], strict=True):
            if ret is None or ret.is_empty:
                continue
            pts = para_px(ret)
            lado = max(np.hypot(*(pts[1] - pts[0])), np.hypot(*(pts[2] - pts[1])))
            if lado < 10:  # visivel na escala do mapa
                c = pts[:4].mean(axis=0)
                pts = c + (pts - c) * (10 / max(lado, 1e-6))
            r, g, b = COR_SEVERIDADE.get(sev, (255, 255, 255))
            pts = pts.round().astype(np.int32)
            cv2.polylines(img, [pts], True, (255, 255, 255), 3, cv2.LINE_AA)
            cv2.polylines(img, [pts], True, (b, g, r), 2, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes() if ok else b""


def _img(dados_bin: bytes, tipo: str = "jpeg") -> str:
    return f"data:image/{tipo};base64," + base64.b64encode(dados_bin).decode()


def _tabela(df: pd.DataFrame, colunas: list[tuple[str, str, str]]) -> str:
    """colunas: (coluna, titulo, formato). formato: txt | int | n1 | n2 | pct | brl | conceito."""
    cab = "".join(f"<th>{html.escape(t)}</th>" for _, t, _ in colunas)
    linhas = []
    for linha in df.itertuples(index=False):
        cels = []
        for col, _, f in colunas:
            v = getattr(linha, col)
            if f == "txt":
                cels.append(f"<td>{html.escape(str(v))}</td>")
            elif f == "conceito":
                cor = COR_CONCEITO.get(str(v), "#888")
                cels.append(
                    f'<td><span class="selo" style="background:{cor}">{html.escape(str(v))}</span></td>'
                )
            else:
                txt = {
                    "int": lambda x: dados.fmt(x, 0),
                    "n1": lambda x: dados.fmt(x, 1),
                    "n2": lambda x: dados.fmt(x, 2),
                    "pct": lambda x: dados.fmt(x, 2) + " %",
                    "brl": dados.reais,
                }[f](float(v))
                cels.append(f'<td class="num">{txt}</td>')
        linhas.append("<tr>" + "".join(cels) + "</tr>")
    return f"<table><thead><tr>{cab}</tr></thead><tbody>{''.join(linhas)}</tbody></table>"


CSS = """
body{font-family:-apple-system,'Segoe UI',Roboto,Arial,sans-serif;color:#1E2315;margin:0;background:#fff}
.pagina{max-width:1000px;margin:0 auto;padding:28px 32px}
.topo{background:FAIXA;color:#fff;padding:16px 32px;display:flex;align-items:center;gap:20px;
border-bottom:3px solid OURO;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.topo img{height:78px;flex:none} .topo div{flex:1}
.topo h1{margin:0;font-size:26px} .topo p{margin:4px 0 0;opacity:.85;font-size:13px}
h2{font-size:17px;margin:26px 0 10px;border-bottom:2px solid #D3D8BF;padding-bottom:4px}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-top:18px}
.kpi{background:#ECEEDF;border-radius:8px;padding:12px 14px}
.kpi b{display:block;font-size:22px;margin-top:2px} .kpi span{font-size:12px;color:#5A6248}
.selo{display:inline-block;color:#fff;font-weight:700;border-radius:5px;padding:1px 9px}
table{border-collapse:collapse;width:100%;font-size:13px}
th{text-align:left;background:#ECEEDF;padding:6px 8px} td{padding:5px 8px;border-bottom:1px solid #E3E6D3}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.mapa{width:100%;border-radius:8px;border:1px solid #D3D8BF}
.grade{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.grade figure{margin:0} .grade img{width:100%;border-radius:6px} .grade figcaption{font-size:11px;color:#5A6248}
.nota{font-size:12px;color:#5A6248;margin-top:8px} .aviso{background:#FFF4E0;border-left:4px solid #EF6C00;padding:8px 12px;font-size:12px}
@media print{.pagina{padding:10px 0} h2{break-after:avoid} table,figure{break-inside:avoid}}
""".replace("FAIXA", marca.FAIXA).replace("OURO", marca.OURO)


def gerar_html(
    run: Path,
    defeitos: gpd.GeoDataFrame,
    segmentos: gpd.GeoDataFrame,
    resumo: dict,
    params: dict,
    orcamento: dict | None = None,
    escopo: str | None = None,
) -> str:
    """Relatorio do recorte dado (defeitos ja filtrados). `orcamento`: saida de `painel` (opcional).

    `orcamento` = {"total": {...}, "por_servico": DataFrame, "placeholder": bool, "bdi": float}.
    `escopo`: frase que descreve o recorte escolhido (vias, segmentos, defeitos), no topo.
    O mapa mostra so a area dos `segmentos` dados.
    """
    run = Path(run)
    fonte = resumo.get("fonte") or {}
    titulo = fonte.get("titulo") or run.name
    ind = dados.resumir(defeitos, segmentos)
    cond = dados.condicao_geral(defeitos, segmentos, params["condicao"])
    vias = dados.por_via(defeitos, segmentos, params["condicao"])
    piores = dados.condicao_segmentos(defeitos, segmentos, params["condicao"])
    piores = piores[piores["n_buracos"] > 0].sort_values("indice").head(10)
    cor = COR_CONCEITO[cond["conceito"]]

    kpis = [
        (
            "Condição geral",
            f'<span class="selo" style="background:{cor}">{cond["conceito"]}</span> {dados.fmt(cond["indice"], 0)}',
        ),
        ("Defeitos", dados.fmt(ind["n_buracos"], 0)),
        ("Área afetada", dados.fmt(ind["area_total_m2"], 1) + " m²"),
        ("Pista afetada", dados.fmt(ind["pct_afetada"], 2) + " %"),
        ("Defeitos por 100 m", dados.fmt(ind["buracos_por_100m"], 1)),
    ]
    p = [
        f"<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><title>PaviScan - {html.escape(titulo)}</title><style>{CSS}</style></head><body>"
    ]
    p.append(
        f"<div class='topo'>{marca.img('exercito.png', 'Exército Brasileiro')}"
        f"<div><h1>{html.escape(titulo)}</h1><p>Relatório de condição do pavimento · PaviScan · "
        f"imagem {html.escape(str(fonte.get('data') or ''))} · GSD {dados.fmt(100 * resumo['gsd_m'], 1)} cm/px · "
        f"{dados.fmt(float(segmentos['area_pista_m2'].sum()), 0)} m² de pista analisados</p></div>"
        f"{marca.img('gpte2.svg', '2º Grupamento de Engenharia')}</div><div class='pagina'>"
    )
    p.append(
        "<div class='kpis'>"
        + "".join(
            f"<div class='kpi'><span>{html.escape(t)}</span><b>{v}</b></div>" for t, v in kpis
        )
        + "</div>"
    )
    if escopo:
        p.append(f"<p class='nota'><b>Recorte deste relatório:</b> {html.escape(escopo)}</p>")
    mapa = mapa_estatico(run, defeitos, resumo, segmentos=segmentos)
    if mapa:
        p.append(f"<h2>Onde estão os defeitos</h2><img class='mapa' src='{_img(mapa)}'>")
        p.append(
            "<p class='nota'>Cada defeito dentro de um retângulo paralelo à via: amarelo (severidade baixa), "
            "laranja (média), vermelho (alta). Em branco, os segmentos analisados.</p>"
        )
    if len(vias):
        p.append("<h2>Condição por via</h2>")
        p.append(
            _tabela(
                vias,
                [
                    ("via", "Via", "txt"),
                    ("conceito", "Conceito", "conceito"),
                    ("indice", "Índice", "int"),
                    ("extensao_m", "Extensão (m)", "int"),
                    ("n_buracos", "Defeitos", "int"),
                    ("area_buracos_m2", "Área afetada (m²)", "n1"),
                    ("pct_afetada", "% afetada", "pct"),
                ],
            )
        )
    if len(piores):
        p.append("<h2>Trechos prioritários</h2>")
        p.append(
            _tabela(
                piores,
                [
                    ("segmento", "Segmento", "int"),
                    ("via", "Via", "txt"),
                    ("conceito", "Conceito", "conceito"),
                    ("n_buracos", "Defeitos", "int"),
                    ("area_buracos_m2", "Área afetada (m²)", "n1"),
                    ("pct_afetada", "% afetada", "pct"),
                ],
            )
        )
    if orcamento:
        t = orcamento["total"]
        p.append("<h2>Estimativa de reparo (plano recomendado)</h2>")
        if orcamento.get("placeholder"):
            p.append(
                "<p class='aviso'>Preços unitários ilustrativos. Substitua pelos de referência (SICRO, SINAPI ou contrato) antes de usar o valor.</p>"
            )
        p.append(
            _tabela(
                orcamento["por_servico"],
                [
                    ("servico", "Serviço", "txt"),
                    ("unidade", "Un.", "txt"),
                    ("quantidade", "Quantidade", "n2"),
                    ("preco_unit", "Preço unit.", "brl"),
                    ("custo", "Custo", "brl"),
                ],
            )
        )
        p.append(
            f"<p><b>Custo direto:</b> {dados.reais(t['direto'])} · <b>BDI ({dados.fmt(100 * orcamento['bdi'], 0)} %):</b> "
            f"{dados.reais(t['bdi'])} · <b>Total:</b> {dados.reais(t['total'])}</p>"
        )
    maiores = (
        defeitos.sort_values("area_m2", ascending=False).head(8) if len(defeitos) else defeitos
    )
    figs = []
    for d in maiores.itertuples():
        arq = dados.recorte(run, d.id)
        if arq is not None:
            caixa = (
                f" · retângulo {dados.fmt(d.caixa_ao_longo_m)} × {dados.fmt(d.caixa_transversal_m)} m"
                if hasattr(d, "caixa_ao_longo_m")
                else ""
            )
            figs.append(
                f"<figure><img src='{_img(arq.read_bytes())}'><figcaption>#{int(d.id)} · {dados.fmt(d.area_m2)} m²{caixa} · "
                f"{html.escape(dados.ROTULO_CLASSE.get(d.classe, d.classe))} · {html.escape(str(d.via))}</figcaption></figure>"
            )
    if figs:
        p.append("<h2>Maiores defeitos</h2><div class='grade'>" + "".join(figs) + "</div>")
    if "revisao" in defeitos.columns and len(defeitos):
        sit = defeitos["revisao"].value_counts()
        partes = [
            f"{int(sit.get(k, 0))} {rotulo}"
            for k, rotulo in (
                ("confirmado", "confirmadas por revisão"),
                ("manual", "adicionadas à mão"),
                ("pendente", "ainda não revisadas"),
                ("falso_positivo", "marcadas como falso positivo"),
            )
            if int(sit.get(k, 0))
        ]
        p.append(
            f"<p class='nota'><b>Revisão manual:</b> das áreas deste relatório, {', '.join(partes)}.</p>"
        )
    limites = html.escape(resumo.get("limites_detector") or "")
    p.append(
        f"<h2>Como ler este relatório</h2><p class='nota'>{limites} O índice de condição (0 a 100) e os conceitos A a E são "
        "de trabalho, para ordenar trechos; não são IGG nem ICP de norma. Os números são candidatos a defeito detectados "
        "automaticamente e devem ser confirmados em campo ou pelos recortes.</p>"
    )
    credito = ""
    if fonte:
        itens = [fonte.get("produtor"), fonte.get("licenca")]
        itens = [html.escape(str(v)) for v in itens if v]
        if fonte.get("origem"):
            itens.append("via " + html.escape(str(fonte["origem"])))
        credito = f"Imagem: {', '.join(itens)}. " if itens else ""
    p.append(
        f"<p class='nota'>{credito}Detector {html.escape(resumo['detector'])} v{html.escape(resumo['versao'])}.</p></div></body></html>"
    )
    return "".join(p)
