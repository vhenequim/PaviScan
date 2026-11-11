"""Agregacao por segmento e resumo do levantamento."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd


def atribuir_segmento(defeitos: gpd.GeoDataFrame, segmentos: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Cada buraco pertence ao segmento do seu centroide. Tambem define o `id`."""
    d = defeitos.copy()
    if d.empty:
        d["segmento"] = pd.Series(dtype="int64")
        d["via"] = pd.Series(dtype="object")
        d.insert(0, "id", pd.Series(dtype="int64"))
        return d
    cent = gpd.GeoDataFrame(geometry=d.geometry.centroid, crs=d.crs)
    perto = gpd.sjoin_nearest(cent, segmentos[["segmento", "via", "geometry"]], how="left")
    perto = perto[~perto.index.duplicated(keep="first")]
    d["segmento"] = perto["segmento"].astype(int)
    d["via"] = perto["via"]
    d["x"] = cent.geometry.x.round(2)
    d["y"] = cent.geometry.y.round(2)
    geo = cent.to_crs(4326)
    d["lon"] = geo.geometry.x.round(7)
    d["lat"] = geo.geometry.y.round(7)
    d = d.sort_values(["segmento", "x", "y"]).reset_index(drop=True)
    d.insert(0, "id", range(1, len(d) + 1))
    return d


def por_segmento(defeitos: gpd.GeoDataFrame, segmentos: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Acrescenta n_buracos, area_buracos_m2 e pct_afetada a cada segmento."""
    s = segmentos.copy()
    grupo = defeitos.groupby("segmento")["area_m2"] if len(defeitos) else None
    n = grupo.size() if grupo is not None else pd.Series(dtype="int64")
    area = grupo.sum() if grupo is not None else pd.Series(dtype="float64")
    s["n_buracos"] = s["segmento"].map(n).fillna(0).astype(int)
    s["area_buracos_m2"] = s["segmento"].map(area).fillna(0.0).round(4)
    s["pct_afetada"] = (100 * s["area_buracos_m2"] / s["area_pista_m2"]).round(3)
    return s


def resumo(defeitos: gpd.GeoDataFrame, segmentos: gpd.GeoDataFrame, **extra) -> dict:
    """Numeros do levantamento inteiro. `extra` entra como veio (gsd, detector, limites...)."""
    area_total = float(defeitos["area_m2"].sum()) if len(defeitos) else 0.0
    area_pista = float(segmentos["area_pista_m2"].sum())
    comprimento = float(segmentos["comprimento_m"].sum())
    sev = defeitos["severidade"].value_counts().to_dict() if len(defeitos) else {}
    return {
        "n_defeitos": int(len(defeitos)),
        "area_total_m2": round(area_total, 4),
        "area_pista_m2": round(area_pista, 3),
        "pct_afetada": round(100 * area_total / area_pista, 4) if area_pista else 0.0,
        "comprimento_pista_m": round(comprimento, 2),
        "n_segmentos": int(len(segmentos)),
        "por_severidade": {k: int(sev.get(k, 0)) for k in ("baixa", "media", "alta")},
        **extra,
    }
