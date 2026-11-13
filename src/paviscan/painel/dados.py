"""Dados do dashboard: leitura, filtros e indicadores. Funcoes puras, sem Streamlit."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd

SEVERIDADES = ["baixa", "media", "alta"]
ROTULO_SEVERIDADE = {"baixa": "Baixa", "media": "Média", "alta": "Alta"}
ROTULO_CLASSE = {"panela": "Buraco", "solo_exposto": "Revestimento perdido"}
CONCEITOS = ["A", "B", "C", "D", "E"]
ROTULO_SITUACAO = {
    "pendente": "Pendente",
    "confirmado": "Confirmado",
    "falso_positivo": "Falso positivo",
    "manual": "Adicionado à mão",
}
COLUNAS_DEFEITO = {
    "id": "int64",
    "area_m2": "float64",
    "largura_m": "float64",
    "comprimento_m": "float64",
    "confianca": "float64",
    "severidade": "object",
    "segmento": "int64",
    "via": "object",
    "classe": "object",
}


def listar_runs(pasta: Path) -> list[Path]:
    """Levantamentos processados: subpastas (ou a propria pasta) com resumo.json."""
    pasta = Path(pasta)
    if (pasta / "resumo.json").is_file():
        return [pasta]
    if not pasta.is_dir():
        return []
    return sorted(p for p in pasta.iterdir() if (p / "resumo.json").is_file())


def carregar(run: Path) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, dict]:
    """Defeitos, segmentos e resumo de um levantamento."""
    run = Path(run)
    resumo = json.loads((run / "resumo.json").read_text())
    seg = gpd.read_file(run / "segmentos.geojson")
    det = gpd.read_file(run / "defeitos.geojson")
    if "geometry" not in det.columns:
        det = gpd.GeoDataFrame(det, geometry=gpd.GeoSeries([], crs=seg.crs), crs=seg.crs)
    for col, tipo in COLUNAS_DEFEITO.items():
        if col not in det.columns:
            det[col] = "panela" if col == "classe" and len(det) else pd.Series(dtype=tipo)
    if "na_emenda" not in det.columns:
        det["na_emenda"] = False
    if "via" not in seg.columns:
        seg["via"] = "pista"
    if "comprimento_m" not in seg.columns:
        seg["comprimento_m"] = float("nan")
    return det, seg, resumo


def filtrar(
    defeitos: gpd.GeoDataFrame,
    area_min: float | None = None,
    area_max: float | None = None,
    confianca_min: float | None = None,
    severidade: list[str] | None = None,
    segmentos: list[int] | None = None,
    vias: list[str] | None = None,
    classes: list[str] | None = None,
    situacoes: list[str] | None = None,
) -> gpd.GeoDataFrame:
    """Filtra os defeitos. None = nao filtra por aquele criterio."""
    d = defeitos
    m = pd.Series(True, index=d.index)
    if area_min is not None:
        m &= d["area_m2"] >= area_min
    if area_max is not None:
        m &= d["area_m2"] <= area_max
    if confianca_min is not None:
        m &= d["confianca"] >= confianca_min
    if severidade is not None:
        m &= d["severidade"].isin(list(severidade))
    if segmentos is not None:
        m &= d["segmento"].isin([int(s) for s in segmentos])
    if vias is not None and "via" in d.columns:
        m &= d["via"].isin(list(vias))
    if classes is not None and "classe" in d.columns:
        m &= d["classe"].isin(list(classes))
    if situacoes is not None and "revisao" in d.columns:
        m &= d["revisao"].isin(list(situacoes))
    return d[m]


def filtrar_segmentos(
    segmentos: gpd.GeoDataFrame, ids: list[int] | None = None, vias: list[str] | None = None
) -> gpd.GeoDataFrame:
    """Segmentos do recorte escolhido (via e/ou segmento). None = nao filtra.

    E o denominador certo para `resumir` quando ha filtro de via ou de segmento: a %
    afetada e os buracos por 100 m passam a valer para o recorte, nao para o bairro todo.
    """
    m = pd.Series(True, index=segmentos.index)
    if vias is not None and "via" in segmentos.columns:
        m &= segmentos["via"].isin(list(vias))
    if ids is not None:
        m &= segmentos["segmento"].isin([int(i) for i in ids])
    return segmentos[m]


def contagem_severidade(defeitos: gpd.GeoDataFrame) -> dict[str, int]:
    """Numero de buracos por severidade, sempre com as tres faixas."""
    cont = defeitos["severidade"].value_counts() if len(defeitos) else {}
    return {s: int(cont.get(s, 0)) for s in SEVERIDADES}


def comprimento_total_m(segmentos: gpd.GeoDataFrame) -> float:
    """Extensao de pista coberta pelos segmentos."""
    if "comprimento_m" in segmentos.columns and segmentos["comprimento_m"].notna().all():
        return float(segmentos["comprimento_m"].sum())
    return 0.0


def resumir(defeitos: gpd.GeoDataFrame, segmentos: gpd.GeoDataFrame) -> dict:
    """Indicadores do topo do painel. Funciona com defeitos ja filtrados."""
    n = int(len(defeitos))
    area = float(defeitos["area_m2"].sum()) if n else 0.0
    area_pista = float(segmentos["area_pista_m2"].sum())
    extensao = comprimento_total_m(segmentos)
    return {
        "n_buracos": n,
        "area_total_m2": area,
        "pct_afetada": 100 * area / area_pista if area_pista else 0.0,
        "buracos_por_100m": 100 * n / extensao if extensao else 0.0,
        "maior_buraco_m2": float(defeitos["area_m2"].max()) if n else 0.0,
    }


def por_segmento(defeitos: gpd.GeoDataFrame, segmentos: gpd.GeoDataFrame) -> pd.DataFrame:
    """Tabela por segmento recalculada com os defeitos filtrados, na ordem da pista."""
    cols = [c for c in ("segmento", "via", "comprimento_m", "area_pista_m2") if c in segmentos]
    s = pd.DataFrame(segmentos[cols]).sort_values("segmento").reset_index(drop=True)
    g = defeitos.groupby("segmento")["area_m2"] if len(defeitos) else None
    s["n_buracos"] = s["segmento"].map(g.size() if g is not None else {}).fillna(0).astype(int)
    s["area_buracos_m2"] = s["segmento"].map(g.sum() if g is not None else {}).fillna(0.0)
    s["pct_afetada"] = 100 * s["area_buracos_m2"] / s["area_pista_m2"]
    return s


def ranking(defeitos: gpd.GeoDataFrame, segmentos: gpd.GeoDataFrame, n: int = 10) -> pd.DataFrame:
    """Piores segmentos pela % afetada (so os que tem buraco)."""
    s = por_segmento(defeitos, segmentos)
    s = s[s["n_buracos"] > 0].sort_values("pct_afetada", ascending=False)
    return s.head(n).reset_index(drop=True)


def segmentos_filtrados(defeitos: gpd.GeoDataFrame, segmentos: gpd.GeoDataFrame):
    """Segmentos com a geometria e os indicadores recalculados para o filtro atual."""
    tab = por_segmento(defeitos, segmentos).set_index("segmento")
    s = segmentos.copy()
    for col in ("n_buracos", "area_buracos_m2", "pct_afetada"):
        s[col] = s["segmento"].map(tab[col])
    return s


def caminho_ortofoto(run: Path, resumo: dict) -> Path | None:
    """Ortofoto de origem do levantamento, se ainda estiver no disco.

    Vem de `ortofoto_caminho` no resumo; para levantamento antigo, procura no acervo uma
    imagem com o nome do levantamento.
    """
    from paviscan import acervo

    gravado = resumo.get("ortofoto_caminho")
    if gravado and Path(gravado).exists():
        return Path(gravado)
    candidato = acervo.pasta_imagens() / Path(run).name / "orto.tif"
    return candidato if candidato.exists() else None


def recorte(run: Path, ident: int) -> Path | None:
    """Miniatura do defeito: a refeita na revisao, se houver, senao a do detector."""
    for nome in (f"rev_{int(ident)}.jpg", f"{int(ident)}.jpg"):
        arq = Path(run) / "recortes" / nome
        if arq.is_file():
            return arq
    return None


def parametros(config: Path | None = None) -> dict:
    """params.yaml inteiro (para os blocos `condicao` e `orcamento`)."""
    from paviscan import config as config_mod

    return config_mod.carregar(config)


def indice_condicao(pct_afetada, defeitos_por_100m, cfg: dict):
    """Indice de condicao de 0 a 100 (100 = sem defeito). `cfg` e o bloco `condicao`.

    E um indice de trabalho para ordenar trechos: perde `peso_area` pontos por 1 % de area
    afetada e `peso_densidade` pontos por defeito a cada 100 m. Nao e IGG nem ICP de norma.
    """
    nota = 100 - cfg["peso_area"] * pct_afetada - cfg["peso_densidade"] * defeitos_por_100m
    return nota.clip(0, 100) if hasattr(nota, "clip") else max(0.0, min(100.0, nota))


def conceito(indice: float, cfg: dict) -> str:
    """Conceito A a E pela nota minima de cada um em `condicao.conceitos`."""
    for letra in CONCEITOS[:-1]:
        if indice >= cfg["conceitos"][letra]:
            return letra
    return CONCEITOS[-1]


def _objetos(defeitos: gpd.GeoDataFrame, cfg: dict) -> gpd.GeoDataFrame:
    """Defeitos que contam na densidade: classe extensa (`so_area`) pesa so pela area."""
    if "classe" not in defeitos.columns:
        return defeitos
    return defeitos[~defeitos["classe"].isin(cfg.get("so_area", []))]


def condicao_segmentos(
    defeitos: gpd.GeoDataFrame, segmentos: gpd.GeoDataFrame, cfg: dict
) -> pd.DataFrame:
    """`por_segmento` com densidade, indice de condicao e conceito de cada segmento."""
    s = por_segmento(defeitos, segmentos)
    s["n_objetos"] = por_segmento(_objetos(defeitos, cfg), segmentos)["n_buracos"].to_numpy()
    extensao = s["comprimento_m"].where(s["comprimento_m"] > 0) if "comprimento_m" in s else None
    s["defeitos_por_100m"] = (
        (100 * s["n_objetos"] / extensao).fillna(0.0) if extensao is not None else 0.0
    )
    s["indice"] = indice_condicao(s["pct_afetada"], s["defeitos_por_100m"], cfg)
    s["conceito"] = [conceito(v, cfg) for v in s["indice"]]
    return s


def por_via(defeitos: gpd.GeoDataFrame, segmentos: gpd.GeoDataFrame, cfg: dict) -> pd.DataFrame:
    """Uma linha por via: extensao, area, defeitos, % afetada, indice e conceito. Pior primeiro."""
    s = condicao_segmentos(defeitos, segmentos, cfg)
    if "via" not in s:
        s["via"] = "pista"
    if "comprimento_m" not in s:
        s["comprimento_m"] = 0.0
    g = s.groupby("via", as_index=False).agg(
        extensao_m=("comprimento_m", "sum"),
        area_pista_m2=("area_pista_m2", "sum"),
        n_buracos=("n_buracos", "sum"),
        n_objetos=("n_objetos", "sum"),
        area_buracos_m2=("area_buracos_m2", "sum"),
        segmentos=("segmento", "count"),
    )
    g["pct_afetada"] = 100 * g["area_buracos_m2"] / g["area_pista_m2"]
    g["defeitos_por_100m"] = (
        100 * g["n_objetos"] / g["extensao_m"].where(g["extensao_m"] > 0)
    ).fillna(0)
    g["indice"] = indice_condicao(g["pct_afetada"], g["defeitos_por_100m"], cfg)
    g["conceito"] = [conceito(v, cfg) for v in g["indice"]]
    return g.sort_values(["indice", "area_buracos_m2"], ascending=[True, False]).reset_index(
        drop=True
    )


def condicao_geral(defeitos: gpd.GeoDataFrame, segmentos: gpd.GeoDataFrame, cfg: dict) -> dict:
    """Indice e conceito do recorte inteiro."""
    r = resumir(defeitos, segmentos)
    densidade = resumir(_objetos(defeitos, cfg), segmentos)["buracos_por_100m"]
    nota = float(indice_condicao(r["pct_afetada"], densidade, cfg))
    return {"indice": nota, "conceito": conceito(nota, cfg)}


def recorte_relatorio(
    defeitos: gpd.GeoDataFrame,
    segmentos: gpd.GeoDataFrame,
    vias: list[str] | None = None,
    seg_de: int | None = None,
    seg_ate: int | None = None,
    severidades: list[str] | None = None,
    classes: list[str] | None = None,
    so_conferidos: bool = False,
    fora: list[int] | tuple = (),
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Defeitos e segmentos que entram no relatorio. None = nao filtra.

    A area (vias e faixa de segmentos) define os segmentos, que sao o denominador dos
    indicadores; os defeitos sao os desses segmentos que passam nos filtros. `so_conferidos`:
    so confirmados na revisao e adicionados a mao. `fora`: ids tirados um a um.
    """
    s = filtrar_segmentos(segmentos, vias=vias)
    if seg_de is not None:
        s = s[s["segmento"] >= int(seg_de)]
    if seg_ate is not None:
        s = s[s["segmento"] <= int(seg_ate)]
    d = defeitos[defeitos["segmento"].isin(s["segmento"])]
    if severidades is not None:
        d = d[d["severidade"].isin(list(severidades))]
    if classes is not None and "classe" in d.columns:
        d = d[d["classe"].isin(list(classes))]
    if so_conferidos and "revisao" in d.columns:
        d = d[d["revisao"].isin(["confirmado", "manual"])]
    if len(fora):
        d = d[~d["id"].isin([int(i) for i in fora])]
    return d, s


def area_e_extensao(segmentos: gpd.GeoDataFrame) -> dict:
    """Area de pista e extensao cobertas pelos segmentos (para o cabecalho do painel)."""
    return {
        "area_pista_m2": float(segmentos["area_pista_m2"].sum()),
        "extensao_m": comprimento_total_m(segmentos),
        "n_segmentos": int(len(segmentos)),
    }


def contagem_classe(defeitos: gpd.GeoDataFrame) -> dict[str, dict[str, float]]:
    """Por classe de defeito: quantidade e area."""
    if defeitos.empty:
        return {}
    g = defeitos.groupby("classe")["area_m2"].agg(["count", "sum"])
    return {k: {"n": int(v["count"]), "area_m2": float(v["sum"])} for k, v in g.iterrows()}


def reais(valor: float) -> str:
    """Valor em reais no formato brasileiro: R$ 1.234,56."""
    return "R$ " + fmt(valor, 2)


def fmt(valor: float, casas: int = 2) -> str:
    """Numero em formato brasileiro: 1.234,56."""
    txt = f"{valor:,.{casas}f}"
    return txt.replace(",", "X").replace(".", ",").replace("X", ".")


def tabela(defeitos: gpd.GeoDataFrame) -> pd.DataFrame:
    """Buracos sem geometria, colunas na ordem de exibicao."""
    ordem = [
        "id", "revisao", "classe", "severidade", "area_m2", "largura_m", "comprimento_m",
        "caixa_ao_longo_m", "caixa_transversal_m", "confianca",
        "via", "segmento", "na_emenda", "lat", "lon", "x", "y",
    ]  # fmt: skip
    return pd.DataFrame(defeitos[[c for c in ordem if c in defeitos.columns]])


def csv_bytes(defeitos: gpd.GeoDataFrame) -> bytes:
    return tabela(defeitos).to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")


def xlsx_bytes(defeitos: gpd.GeoDataFrame, segmentos: gpd.GeoDataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xl:
        tabela(defeitos).to_excel(xl, sheet_name="Buracos", index=False)
        por_segmento(defeitos, segmentos).to_excel(xl, sheet_name="Segmentos", index=False)
    return buf.getvalue()


def geojson_bytes(defeitos: gpd.GeoDataFrame) -> bytes:
    """GeoJSON em EPSG:4326 (padrao do formato), so para troca de dados."""
    if defeitos.empty:
        return json.dumps({"type": "FeatureCollection", "features": []}).encode()
    return defeitos.to_crs(4326).to_json().encode()


def para_mapa(gdf: gpd.GeoDataFrame, colunas: list[str]) -> dict:
    """FeatureCollection em EPSG:4326 para exibir. Nunca usar para medir."""
    if gdf.empty:
        return {"type": "FeatureCollection", "features": []}
    cols = [c for c in colunas if c in gdf.columns]
    return json.loads(gdf[[*cols, "geometry"]].to_crs(4326).to_json())


def previa_data_url(run: Path, lado_max: int = 4000) -> str | None:
    """previa.png como data URL WebP (leve, com transparencia) para a camada de imagem."""
    from PIL import Image

    arq = Path(run) / "previa.png"
    if not arq.is_file():
        return None
    img = Image.open(arq).convert("RGBA")
    if max(img.size) > lado_max:
        img.thumbnail((lado_max, lado_max))
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=82, method=3)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()
