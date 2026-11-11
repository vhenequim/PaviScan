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
COLUNAS_DEFEITO = {
    "id": "int64",
    "area_m2": "float64",
    "largura_m": "float64",
    "comprimento_m": "float64",
    "confianca": "float64",
    "severidade": "object",
    "segmento": "int64",
    "via": "object",
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
            det[col] = pd.Series(dtype=tipo)
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
) -> gpd.GeoDataFrame:
    """Filtra os buracos. None = nao filtra por aquele criterio."""
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


def fmt(valor: float, casas: int = 2) -> str:
    """Numero em formato brasileiro: 1.234,56."""
    txt = f"{valor:,.{casas}f}"
    return txt.replace(",", "X").replace(".", ",").replace("X", ".")


def tabela(defeitos: gpd.GeoDataFrame) -> pd.DataFrame:
    """Buracos sem geometria, colunas na ordem de exibicao."""
    ordem = [
        "id", "severidade", "area_m2", "largura_m", "comprimento_m", "confianca",
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
