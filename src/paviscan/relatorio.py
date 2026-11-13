"""Relatorio executivo de um levantamento: uma pagina HTML autocontida, pronta para imprimir."""

from __future__ import annotations

import base64
import html
from pathlib import Path

import cv2
import geopandas as gpd
import numpy as np
import pandas as pd

from . import marca
from .painel import dados

COR_SEVERIDADE = {"baixa": (227, 178, 60), "media": (238, 123, 48), "alta": (214, 40, 57)}
COR_CONCEITO = {"A": "#2E7D32", "B": "#7CB342", "C": "#F9A825", "D": "#EF6C00", "E": "#C62828"}


def mapa_estatico(
    run: Path, defeitos: gpd.GeoDataFrame, resumo: dict, lado_px: int = 1500
) -> bytes:
    """JPEG da ortofoto com um anel por defeito, colorido pela severidade."""
    img = cv2.imread(str(Path(run) / "previa.png"), cv2.IMREAD_UNCHANGED)
    if img is None:
        return b""
    h, w = img.shape[:2]
    if img.shape[2] == 4:  # fundo branco onde nao ha dado
        alfa = img[..., 3:4].astype(np.float32) / 255.0
        img = (img[..., :3].astype(np.float32) * alfa + 255.0 * (1 - alfa)).astype(np.uint8)
    f = min(1.0, lado_px / max(h, w))
    img = cv2.resize(img, (max(1, int(w * f)), max(1, int(h * f))), interpolation=cv2.INTER_AREA)
    (lat_s, lon_o), (lat_n, lon_l) = resumo["previa_limites"]
    if len(defeitos):
        d = defeitos.sort_values("area_m2")  # os maiores por cima
        for lon, lat, sev in zip(d["lon"], d["lat"], d["severidade"], strict=True):
            x = int((lon - lon_o) / (lon_l - lon_o) * img.shape[1])
            y = int((lat_n - lat) / (lat_n - lat_s) * img.shape[0])
            r, g, b = COR_SEVERIDADE.get(sev, (255, 255, 255))
            cv2.circle(img, (x, y), 7, (255, 255, 255), 3, cv2.LINE_AA)
            cv2.circle(img, (x, y), 7, (b, g, r), 2, cv2.LINE_AA)
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
) -> str:
    """Relatorio do recorte dado (defeitos ja filtrados). `orcamento`: saida de `painel` (opcional).

    `orcamento` = {"total": {...}, "por_servico": DataFrame, "placeholder": bool, "bdi": float}.
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
    mapa = mapa_estatico(run, defeitos, resumo)
    if mapa:
        p.append(f"<h2>Onde estão os defeitos</h2><img class='mapa' src='{_img(mapa)}'>")
        p.append(
            "<p class='nota'>Um anel por defeito: amarelo (severidade baixa), laranja (média), vermelho (alta).</p>"
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
            figs.append(
                f"<figure><img src='{_img(arq.read_bytes())}'><figcaption>#{int(d.id)} · {dados.fmt(d.area_m2)} m² · "
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
