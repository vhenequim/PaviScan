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
        grupos = geometria.retangulos(defeitos).sort_values("area_m2")  # os maiores por cima
        rets = grupos.geometry.to_crs(4326)
        for ret, sev in zip(rets, grupos["severidade"], strict=True):
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
.kpis{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin-top:18px}
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


COLUNAS_DEFEITOS = [
    ("id", "Id", "int"),
    ("tipo", "Tipo", "txt"),
    ("severidade", "Severidade", "txt"),
    ("area_m2", "Área (m²)", "n2"),
    ("retangulo", "Retângulo na via (m)", "txt"),
    ("grupo", "Remendo", "int"),
    ("via", "Via", "txt"),
    ("segmento", "Seg.", "int"),
    ("revisao", "Revisão", "txt"),
]


def recortes_que_faltam(
    run: Path, defeitos: gpd.GeoDataFrame, resumo: dict, params: dict, ids
) -> int:
    """Gera a miniatura dos defeitos `ids` que ainda nao tem (o pipeline so grava as
    `recortes.maximo` maiores). Precisa da ortofoto; sem ela, nao faz nada. Devolve quantas."""
    faltam = [int(i) for i in ids if dados.recorte(run, int(i)) is None]
    orto = dados.caminho_ortofoto(Path(run), resumo) if faltam else None
    if not faltam or orto is None:
        return 0
    from . import exportar, ingestao

    d = defeitos.set_index(defeitos["id"].astype(int), drop=False)
    rets = geometria.retangulo_de_cada(d)
    pasta = Path(run) / "recortes"
    pasta.mkdir(exist_ok=True)
    feitos = 0
    with ingestao.abrir(orto) as ds:
        escala = ingestao.escala_uint8(ds, params["raster"])
        for i in faltam:
            if i in d.index:
                geom = d.geometry.loc[i]
                feitos += bool(
                    exportar.recorte(
                        ds, geom, pasta / f"{i}.jpg", params["recortes"], escala, rets.loc[i]
                    )
                )
    return feitos


def conteudo(
    run: Path,
    defeitos: gpd.GeoDataFrame,
    segmentos: gpd.GeoDataFrame,
    resumo: dict,
    params: dict,
    orcamento: dict | None = None,
    escopo: str | None = None,
    imagens=None,
    gerar_recortes: bool = True,
) -> dict:
    """Tudo o que vai no relatorio, pronto para desenhar em HTML ou PDF.

    `defeitos`: os que entram (ja filtrados), todos listados. `imagens`: ids que aparecem
    com miniatura (None = todos). `orcamento` = {"total": {...}, "por_servico": DataFrame,
    "placeholder": bool, "bdi": float}. `escopo`: frase do recorte escolhido.
    """
    run = Path(run)
    fonte = resumo.get("fonte") or {}
    defeitos = geometria.agrupar_tudo(defeitos, params) if len(defeitos) else defeitos
    ind = dados.resumir(defeitos, segmentos)
    cond = dados.condicao_geral(defeitos, segmentos, params["condicao"])
    piores = dados.condicao_segmentos(defeitos, segmentos, params["condicao"])
    n_ret = int(defeitos["grupo"].nunique()) if len(defeitos) else 0
    ordem = defeitos.sort_values(["via", "segmento", "id"]) if len(defeitos) else defeitos
    tabela = pd.DataFrame(
        {
            "id": ordem["id"].astype(int) if len(ordem) else [],
            "tipo": [dados.ROTULO_CLASSE.get(c, c) for c in ordem.get("classe", [])],
            "severidade": [dados.ROTULO_SEVERIDADE.get(s, s) for s in ordem.get("severidade", [])],
            "area_m2": ordem["area_m2"] if len(ordem) else [],
            "retangulo": [
                f"{dados.fmt(a)} × {dados.fmt(b)}"
                for a, b in zip(
                    ordem.get("caixa_ao_longo_m", ordem.get("comprimento_m", [])),
                    ordem.get("caixa_transversal_m", ordem.get("largura_m", [])),
                    strict=True,
                )
            ],
            "grupo": ordem["grupo"] if len(ordem) else [],
            "via": ordem["via"].astype(str) if len(ordem) else [],
            "segmento": ordem["segmento"] if len(ordem) else [],
            "revisao": [
                dados.ROTULO_SITUACAO.get(r, r)
                for r in ordem.get("revisao", ["pendente"] * len(ordem))
            ],
        }
    )
    com_imagem = [int(i) for i in tabela["id"] if imagens is None or int(i) in imagens]
    if gerar_recortes and com_imagem:
        recortes_que_faltam(run, defeitos, resumo, params, com_imagem)
    figuras = []
    por_id = ordem.set_index(ordem["id"].astype(int), drop=False) if len(ordem) else ordem
    for i in com_imagem:
        arq = dados.recorte(run, i)
        if arq is None:
            continue
        d = por_id.loc[i]
        legenda = (
            f"#{i} · {dados.ROTULO_CLASSE.get(d['classe'], d['classe'])} · {dados.fmt(d['area_m2'])} m² · "
            f"remendo {int(d['grupo'])} · {d['via']}"
        )
        figuras.append((i, arq.read_bytes(), legenda))
    revisao_txt = None
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
        revisao_txt = f"Das áreas deste relatório, {', '.join(partes)}."
    itens = [str(v) for v in (fonte.get("produtor"), fonte.get("licenca")) if v]
    if fonte.get("origem"):
        itens.append("via " + str(fonte["origem"]))
    credito = f"Imagem: {', '.join(itens)}. " if itens else ""
    return {
        "titulo": fonte.get("titulo") or run.name,
        "subtitulo": (
            f"Relatório de condição do pavimento · PaviScan · imagem {fonte.get('data') or ''} · "
            f"GSD {dados.fmt(100 * resumo['gsd_m'], 1)} cm/px · "
            f"{dados.fmt(float(segmentos['area_pista_m2'].sum()), 0)} m² de pista analisados"
        ),
        "condicao": cond,
        "kpis": [
            ("Defeitos", dados.fmt(ind["n_buracos"], 0)),
            ("Retângulos de reparo", dados.fmt(n_ret, 0)),
            ("Área afetada", dados.fmt(ind["area_total_m2"], 1) + " m²"),
            ("Pista afetada", dados.fmt(ind["pct_afetada"], 2) + " %"),
            ("Defeitos por 100 m", dados.fmt(ind["buracos_por_100m"], 1)),
        ],
        "escopo": escopo,
        "mapa": mapa_estatico(run, defeitos, resumo, segmentos=segmentos),
        "nota_mapa": (
            "Cada retângulo é um remendo, paralelo à via; defeitos que se tocam ou que são "
            "muito pequenos e próximos ficam no mesmo. Amarelo: severidade baixa; laranja: média; "
            "vermelho: alta. Em branco, os segmentos analisados."
        ),
        "vias": dados.por_via(defeitos, segmentos, params["condicao"]),
        "piores": piores[piores["n_buracos"] > 0].sort_values("indice").head(10),
        "orcamento": orcamento,
        "tabela": tabela,
        "figuras": figuras,
        "sem_figura": len(com_imagem) - len(figuras),
        "revisao": revisao_txt,
        "limites": resumo.get("limites_detector") or "",
        "rodape": f"{credito}Detector {resumo['detector']} v{resumo['versao']}.",
    }


COLUNAS_VIAS = [
    ("via", "Via", "txt"),
    ("conceito", "Conceito", "conceito"),
    ("indice", "Índice", "int"),
    ("extensao_m", "Extensão (m)", "int"),
    ("n_buracos", "Defeitos", "int"),
    ("area_buracos_m2", "Área afetada (m²)", "n1"),
    ("pct_afetada", "% afetada", "pct"),
]
COLUNAS_PIORES = [
    ("segmento", "Segmento", "int"),
    ("via", "Via", "txt"),
    ("conceito", "Conceito", "conceito"),
    ("n_buracos", "Defeitos", "int"),
    ("area_buracos_m2", "Área afetada (m²)", "n1"),
    ("pct_afetada", "% afetada", "pct"),
]
COLUNAS_ORCAMENTO = [
    ("servico", "Serviço", "txt"),
    ("unidade", "Un.", "txt"),
    ("quantidade", "Quantidade", "n2"),
    ("preco_unit", "Preço unit.", "brl"),
    ("custo", "Custo", "brl"),
]
LEITURA = (
    "O índice de condição (0 a 100) e os conceitos A a E são de trabalho, para ordenar trechos; "
    "não são IGG nem ICP de norma. Os números são candidatos a defeito detectados automaticamente "
    "e devem ser confirmados em campo ou pelos recortes."
)
AVISO_PRECOS = (
    "Preços unitários ilustrativos. Substitua pelos de referência (SICRO, SINAPI ou contrato) "
    "antes de usar o valor."
)


def gerar_html(
    run: Path,
    defeitos: gpd.GeoDataFrame,
    segmentos: gpd.GeoDataFrame,
    resumo: dict,
    params: dict,
    orcamento: dict | None = None,
    escopo: str | None = None,
    imagens=None,
    gerar_recortes: bool = True,
) -> str:
    """Relatorio em HTML autocontido (imprime em PDF pelo navegador). Ver `conteudo`."""
    c = conteudo(
        run, defeitos, segmentos, resumo, params, orcamento, escopo, imagens, gerar_recortes
    )
    e = html.escape
    cond = c["condicao"]
    selo = f'<span class="selo" style="background:{COR_CONCEITO[cond["conceito"]]}">{cond["conceito"]}</span>'
    kpis = [("Condição geral", f"{selo} {dados.fmt(cond['indice'], 0)}")] + [
        (t, e(v)) for t, v in c["kpis"]
    ]
    p = [
        f"<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><title>PaviScan - {e(c['titulo'])}</title><style>{CSS}</style></head><body>",
        f"<div class='topo'>{marca.img('exercito.png', 'Exército Brasileiro')}"
        f"<div><h1>{e(c['titulo'])}</h1><p>{e(c['subtitulo'])}</p></div>"
        f"{marca.img('gpte2.svg', '2º Grupamento de Engenharia')}</div><div class='pagina'>",
        "<div class='kpis'>"
        + "".join(f"<div class='kpi'><span>{e(t)}</span><b>{v}</b></div>" for t, v in kpis)
        + "</div>",
    ]
    if c["escopo"]:
        p.append(f"<p class='nota'><b>Recorte deste relatório:</b> {e(c['escopo'])}</p>")
    if c["mapa"]:
        p.append(f"<h2>Onde estão os defeitos</h2><img class='mapa' src='{_img(c['mapa'])}'>")
        p.append(f"<p class='nota'>{e(c['nota_mapa'])}</p>")
    if len(c["vias"]):
        p.append("<h2>Condição por via</h2>" + _tabela(c["vias"], COLUNAS_VIAS))
    if len(c["piores"]):
        p.append("<h2>Trechos prioritários</h2>" + _tabela(c["piores"], COLUNAS_PIORES))
    orc = c["orcamento"]
    if orc:
        t = orc["total"]
        p.append("<h2>Estimativa de reparo (plano recomendado)</h2>")
        if orc.get("placeholder"):
            p.append(f"<p class='aviso'>{AVISO_PRECOS}</p>")
        p.append(_tabela(orc["por_servico"], COLUNAS_ORCAMENTO))
        p.append(
            f"<p><b>Custo direto:</b> {dados.reais(t['direto'])} · <b>BDI ({dados.fmt(100 * orc['bdi'], 0)} %):</b> "
            f"{dados.reais(t['bdi'])} · <b>Total:</b> {dados.reais(t['total'])}</p>"
        )
    if len(c["tabela"]):
        p.append(
            f"<h2>Defeitos ({dados.fmt(len(c['tabela']), 0)})</h2>"
            + _tabela(c["tabela"], COLUNAS_DEFEITOS)
        )
    if c["figuras"]:
        figs = "".join(
            f"<figure><img src='{_img(b)}'><figcaption>{e(legenda)}</figcaption></figure>"
            for _, b, legenda in c["figuras"]
        )
        p.append(
            f"<h2>Imagens dos defeitos ({dados.fmt(len(c['figuras']), 0)})</h2><div class='grade'>{figs}</div>"
        )
        p.append(
            "<p class='nota'>Contorno do defeito em vermelho; o retângulo de reparo dele em amarelo.</p>"
        )
    if c["sem_figura"]:
        p.append(
            f"<p class='nota'>{c['sem_figura']} defeito(s) sem imagem: a ortofoto não estava disponível para gerar o recorte.</p>"
        )
    if c["revisao"]:
        p.append(f"<p class='nota'><b>Revisão manual:</b> {e(c['revisao'])}</p>")
    p.append(f"<h2>Como ler este relatório</h2><p class='nota'>{e(c['limites'])} {LEITURA}</p>")
    p.append(f"<p class='nota'>{e(c['rodape'])}</p></div></body></html>")
    return "".join(p)


# ============================================================== PDF
_TROCAS_LATIN1 = {
    "–": "-", "—": "-", "≥": ">=", "≤": "<=", "“": '"', "”": '"', "‘": "'", "’": "'",
    "…": "...", "•": "-", "✓": "v", "✗": "x", "→": "->", " ": " ", " ": " ",
}  # fmt: skip


def _l1(txt) -> str:
    """Texto que cabe nas fontes padrao do PDF (Latin-1, que cobre o portugues)."""
    s = str(txt)
    for a, b in _TROCAS_LATIN1.items():
        s = s.replace(a, b)
    return s.encode("latin-1", "replace").decode("latin-1")


def _rgb(hexa: str) -> tuple[int, int, int]:
    return tuple(int(hexa[i : i + 2], 16) for i in (1, 3, 5))


def _celula(v, formato: str) -> str:
    if formato in ("txt", "conceito"):
        return str(v)
    return {
        "int": lambda x: dados.fmt(x, 0),
        "n1": lambda x: dados.fmt(x, 1),
        "n2": lambda x: dados.fmt(x, 2),
        "pct": lambda x: dados.fmt(x, 2) + " %",
        "brl": dados.reais,
    }[formato](float(v))


def gerar_pdf(
    run: Path,
    defeitos: gpd.GeoDataFrame,
    segmentos: gpd.GeoDataFrame,
    resumo: dict,
    params: dict,
    orcamento: dict | None = None,
    escopo: str | None = None,
    imagens=None,
    gerar_recortes: bool = True,
) -> bytes:
    """O mesmo relatorio de `gerar_html`, ja em PDF (A4), sem passar pelo navegador."""
    import io

    from fpdf import FPDF
    from fpdf.fonts import FontFace

    c = conteudo(
        run, defeitos, segmentos, resumo, params, orcamento, escopo, imagens, gerar_recortes
    )
    oliva, claro, cinza = _rgb("#3F4B22"), _rgb("#ECEEDF"), (90, 98, 72)

    class Pdf(FPDF):
        def footer(self):
            self.set_y(-10)
            self.set_font("Helvetica", size=7)
            self.set_text_color(*cinza)
            self.cell(
                0,
                5,
                _l1(f"PaviScan · {c['titulo']} · página {self.page_no()} de {{nb}}"),
                align="C",
            )

    pdf = Pdf(orientation="P", unit="mm", format="A4")
    pdf.set_title(_l1(f"PaviScan - {c['titulo']}"))
    pdf.set_margins(12, 12, 12)
    pdf.set_auto_page_break(True, margin=14)
    pdf.add_page()
    largura = pdf.epw

    # faixa de titulo com os dois simbolos
    pdf.set_fill_color(*oliva)
    pdf.rect(0, 0, 210, 30, style="F")
    pdf.set_fill_color(*_rgb(marca.OURO))
    pdf.rect(0, 30, 210, 1, style="F")
    for nome, x in (("exercito.png", 8), ("gpte2.svg", 180)):
        try:
            pdf.image(str(marca.PASTA / nome), x=x, y=4, h=22)
        except Exception:  # noqa: BLE001 (simbolo que o fpdf2 nao le: relatorio sai sem ele)
            pass
    pdf.set_text_color(255, 255, 255)
    titulo, tamanho = _l1(c["titulo"]), 15.0
    pdf.set_font("Helvetica", "B", tamanho)
    while pdf.get_string_width(titulo) > 142 * 1.8 and tamanho > 9:  # cabe em 2 linhas
        tamanho -= 1
        pdf.set_font("Helvetica", "B", tamanho)
    pdf.set_xy(34, 5)
    pdf.multi_cell(142, tamanho * 0.42, titulo, max_line_height=tamanho * 0.42)
    pdf.set_x(34)
    pdf.set_font("Helvetica", size=7.5)
    pdf.multi_cell(142, 3.6, _l1(c["subtitulo"]))

    # indicadores
    cond = c["condicao"]
    kpis = [("Condição geral", f"{cond['conceito']}  {dados.fmt(cond['indice'], 0)}")] + c["kpis"]
    y0, gap = 36, 2.0
    bw = (largura - gap * (len(kpis) - 1)) / len(kpis)
    for k, (rotulo, valor) in enumerate(kpis):
        x = 12 + k * (bw + gap)
        pdf.set_fill_color(*claro)
        pdf.rect(x, y0, bw, 15, style="F")
        pdf.set_text_color(*cinza)
        pdf.set_font("Helvetica", size=6.5)
        pdf.set_xy(x + 2, y0 + 2)
        pdf.cell(bw - 4, 3, _l1(rotulo))
        pdf.set_xy(x + 2, y0 + 7)
        if k == 0:
            pdf.set_fill_color(*_rgb(COR_CONCEITO[cond["conceito"]]))
            pdf.set_text_color(255, 255, 255)
            pdf.set_font("Helvetica", "B", 11)
            pdf.cell(7, 6, _l1(cond["conceito"]), align="C", fill=True)
            pdf.set_text_color(30, 35, 21)
            pdf.cell(bw - 11, 6, " " + dados.fmt(cond["indice"], 0))
        else:
            pdf.set_text_color(30, 35, 21)
            pdf.set_font("Helvetica", "B", 11)
            pdf.cell(bw - 4, 6, _l1(valor))
    pdf.set_y(y0 + 18)

    def nota(texto: str, negrito: str = "") -> None:
        pdf.set_text_color(*cinza)
        pdf.set_font("Helvetica", size=7.5)
        pdf.multi_cell(
            0, 3.8, _l1(f"**{negrito}** {texto}" if negrito else texto),
            markdown=bool(negrito), align="L",
        )  # fmt: skip
        pdf.ln(1)

    def secao(titulo: str, espaco: float = 30) -> None:
        if pdf.get_y() + espaco > pdf.page_break_trigger:
            pdf.add_page()
        pdf.ln(3)
        pdf.set_text_color(30, 35, 21)
        pdf.set_font("Helvetica", "B", 11.5)
        pdf.cell(0, 6, _l1(titulo), new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(*_rgb("#D3D8BF"))
        pdf.set_line_width(0.5)
        pdf.line(12, pdf.get_y(), 12 + largura, pdf.get_y())
        pdf.ln(2)

    def tabela(df: pd.DataFrame, colunas: list[tuple[str, str, str]], larguras=None) -> None:
        pdf.set_font("Helvetica", size=7.5)
        pdf.set_text_color(30, 35, 21)
        pdf.set_fill_color(255, 255, 255)  # linha nao herda a cor do bloco anterior
        pdf.set_draw_color(*_rgb("#E3E6D3"))
        pdf.set_line_width(0.2)
        alinhar = tuple("LEFT" if f in ("txt", "conceito") else "RIGHT" for _, _, f in colunas)
        with pdf.table(
            col_widths=larguras or tuple(1 for _ in colunas),
            text_align=alinhar,
            headings_style=FontFace(emphasis="BOLD", fill_color=claro),
            borders_layout="HORIZONTAL_LINES",
            line_height=4.2,
            padding=0.8,
        ) as t:
            linha = t.row()
            for _, titulo, _ in colunas:
                linha.cell(_l1(titulo))
            for r in df.itertuples(index=False):
                linha = t.row()
                for col, _, f in colunas:
                    v = getattr(r, col)
                    if f == "conceito":
                        estilo = FontFace(emphasis="BOLD", color=(255, 255, 255),
                                          fill_color=_rgb(COR_CONCEITO.get(str(v), "#888888")))  # fmt: skip
                        linha.cell(_l1(v), style=estilo)
                    else:
                        linha.cell(_l1(_celula(v, f)))
        pdf.ln(2)

    if c["escopo"]:
        nota(c["escopo"], "Recorte deste relatório:")
    if c["mapa"]:
        secao("Onde estão os defeitos", 80)
        arr = cv2.imdecode(np.frombuffer(c["mapa"], np.uint8), cv2.IMREAD_COLOR)
        alt_px, larg_px = arr.shape[:2]
        w, h = largura, largura * alt_px / larg_px
        teto = pdf.page_break_trigger - pdf.get_y() - 10
        if h > teto:  # cabe no resto da pagina, sem cortar
            if teto < 90:
                pdf.add_page()
                teto = pdf.page_break_trigger - pdf.get_y() - 10
            w, h = (w * teto / h, teto) if h > teto else (w, h)
        pdf.image(io.BytesIO(c["mapa"]), x=12 + (largura - w) / 2, y=pdf.get_y(), w=w, h=h)
        pdf.set_y(pdf.get_y() + h + 1.5)
        nota(c["nota_mapa"])
    if len(c["vias"]):
        secao("Condição por via")
        tabela(c["vias"], COLUNAS_VIAS, (5, 1.1, 1.2, 1.6, 1.4, 2, 1.6))
    if len(c["piores"]):
        secao("Trechos prioritários")
        tabela(c["piores"], COLUNAS_PIORES, (1.3, 5, 1.1, 1.4, 2, 1.6))
    orc = c["orcamento"]
    if orc:
        secao("Estimativa de reparo (plano recomendado)")
        if orc.get("placeholder"):
            pdf.set_fill_color(255, 244, 224)
            pdf.set_text_color(120, 60, 0)
            pdf.set_font("Helvetica", size=7.5)
            pdf.multi_cell(0, 4, _l1(AVISO_PRECOS), fill=True)
            pdf.ln(1.5)
        tabela(orc["por_servico"], COLUNAS_ORCAMENTO, (5, 1, 1.8, 1.8, 2))
        t = orc["total"]
        pdf.set_text_color(30, 35, 21)
        pdf.set_font("Helvetica", size=8.5)
        pdf.multi_cell(
            0, 4.5,
            _l1(f"**Custo direto:** {dados.reais(t['direto'])} · **BDI ({dados.fmt(100 * orc['bdi'], 0)} %):** "
                f"{dados.reais(t['bdi'])} · **Total:** {dados.reais(t['total'])}"),
            markdown=True,
        )  # fmt: skip
    if len(c["tabela"]):
        secao(f"Defeitos ({dados.fmt(len(c['tabela']), 0)})")
        tabela(c["tabela"], COLUNAS_DEFEITOS, (1, 2.4, 1.3, 1.2, 2, 1.1, 3.2, 0.8, 1.8))
    if c["figuras"]:
        secao(f"Imagens dos defeitos ({dados.fmt(len(c['figuras']), 0)})", 60)
        nota("Contorno do defeito em vermelho; o retângulo de reparo dele em amarelo.")
        por_linha, gap = 4, 3.0
        fw = (largura - gap * (por_linha - 1)) / por_linha
        for k in range(0, len(c["figuras"]), por_linha):
            if pdf.get_y() + fw + 9 > pdf.page_break_trigger:
                pdf.add_page()
            y = pdf.get_y()
            for j, (_, imagem, legenda) in enumerate(c["figuras"][k : k + por_linha]):
                x = 12 + j * (fw + gap)
                pdf.image(io.BytesIO(imagem), x=x, y=y, w=fw, h=fw)
                pdf.set_xy(x, y + fw + 0.6)
                pdf.set_text_color(*cinza)
                pdf.set_font("Helvetica", size=5.8)
                pdf.multi_cell(fw, 2.6, _l1(legenda), align="L")
            pdf.set_y(y + fw + 9)
    if c["sem_figura"]:
        nota(
            f"{c['sem_figura']} defeito(s) sem imagem: a ortofoto não estava disponível para gerar o recorte."
        )
    if c["revisao"]:
        nota(c["revisao"], "Revisão manual:")
    secao("Como ler este relatório", 20)
    nota(f"{c['limites']} {LEITURA}")
    nota(c["rodape"])
    return bytes(pdf.output())
