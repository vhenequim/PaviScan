"""Orquestra: ortofoto + pista -> defeitos, segmentos, resumo e previa numa pasta."""

from __future__ import annotations

import json
import math
import warnings
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from rasterio.windows import transform as transform_da_janela
from shapely.geometry import box
from shapely.ops import unary_union
from shapely.prepared import prep

from . import config as config_mod
from . import exportar, geometria, indicadores, ingestao
from . import pista as pista_mod
from .deteccao import Deteccao, criar

Progresso = Callable[[float, str], None]


def _inteiras(
    dets: list[Deteccao], janela, largura: int, altura: int, sobre: int, margem: int
) -> tuple[list[Deteccao], list[float], list[bool]]:
    """Decide o que cada janela entrega perto das suas bordas internas.

    A menos de `margem` px de uma borda interna o detector perde contexto (o fundo local
    fica sem apoio) e o objeto pode sair truncado. Tres casos:
    - longe das bordas internas: fica;
    - perto da borda e pequena (ate `sobre - 2 * margem` px): descartada, porque a janela
      vizinha a ve inteira e longe da borda;
    - perto da borda e maior que isso: fica, marcada como "na emenda" (pode estar
      truncada). Entre as versoes das janelas vizinhas, `geometria.deduplicar` fica com
      a maior.
    Devolve (deteccoes, folga de cada uma em px ate a borda interna, marca de emenda).
    """
    corta_topo, corta_esq = janela.row_off > 0, janela.col_off > 0
    corta_base = janela.row_off + janela.height < altura
    corta_dir = janela.col_off + janela.width < largura
    perto, cabe = max(1, margem), sobre - 2 * margem
    boas, folgas, emendas = [], [], []
    for d in dets:
        linhas = d.mascara.any(axis=1).nonzero()[0]
        colunas = d.mascara.any(axis=0).nonzero()[0]
        if linhas.size == 0:
            continue
        h, w = d.mascara.shape
        l0, l1, c0, c1 = int(linhas[0]), int(linhas[-1]), int(colunas[0]), int(colunas[-1])
        folga_v = min(l0 if corta_topo else math.inf, h - 1 - l1 if corta_base else math.inf)
        folga_h = min(c0 if corta_esq else math.inf, w - 1 - c1 if corta_dir else math.inf)
        na_v, na_h = folga_v < perto, folga_h < perto
        if na_v or na_h:
            pequena = (not na_v or l1 - l0 + 1 <= cabe) and (not na_h or c1 - c0 + 1 <= cabe)
            if pequena:
                continue
        boas.append(d)
        folgas.append(min(folga_v, folga_h))
        emendas.append(na_v or na_h)
    return boas, folgas, emendas


def _fonte(orto: Path) -> dict | None:
    """Credito da imagem, se houver um stac_item.json do OpenAerialMap ao lado dela."""
    arq = orto.parent / "stac_item.json"
    if not arq.is_file():
        return None
    try:
        item = json.loads(arq.read_text())
        item = item["features"][0] if item.get("type") == "FeatureCollection" else item
        p = item["properties"]
    except (ValueError, KeyError, IndexError):
        return None  # arquivo de credito ilegivel: segue sem credito, nao afeta as medidas
    return {
        "titulo": p.get("title"),
        "produtor": p.get("oam:producer_name"),
        "licenca": p.get("license"),
        "data": (p.get("start_datetime") or p.get("datetime") or "")[:10],
        "origem": "OpenAerialMap",
    }


def processar(
    orto: Path,
    pista: Path,
    saida: Path,
    config: Path | None = None,
    progresso: Progresso | None = None,
) -> Path:
    """Processa uma ortofoto e grava o levantamento em `saida`. Devolve `saida`."""
    orto, pista, saida = Path(orto), Path(pista), Path(saida)
    params = config_mod.carregar(config)
    avisar = progresso or (lambda fracao, texto: None)

    with ingestao.abrir(orto) as ds:
        gsd = ingestao.gsd_m(ds)
        escala = ingestao.escala_uint8(ds, params["raster"])
        # pista so conta onde a ortofoto tem dado; fora disso nao ha o que medir
        pista_gdf = pista_mod.recortar(
            pista_mod.carregar(pista, ds.crs, params["pista"]["aviso_sobreposicao"]),
            ingestao.area_com_dado(ds, params["pista"]["res_recorte_m"]),
        )
        pista_prep = prep(unary_union(list(pista_gdf.geometry)))
        detector = criar(params["detector"], params.get(params["detector"]))
        saida.mkdir(parents=True, exist_ok=True)

        t = params["tiles"]
        sobre = max(int(t["sobreposicao_px"]), math.ceil(t["sobreposicao_min_m"] / gsd))
        tamanho = max(int(t["tamanho_px"]), 2 * sobre)  # a janela cresce, a sobreposicao nao cede
        margem = math.ceil(t["margem_emenda_m"] / gsd)
        todas = list(ingestao.janelas(ds.width, ds.height, tamanho, sobre))

        itens: list[geometria.Item] = []
        for i, janela in enumerate(todas):
            tf = transform_da_janela(janela, ds.transform)
            esq, topo = tf @ (0, 0)
            dir_, base = tf @ (janela.width, janela.height)
            if not pista_prep.intersects(box(esq, base, dir_, topo)):
                continue
            forma = (int(janela.height), int(janela.width))
            valida = pista_mod.mascara(pista_gdf, tf, forma)
            if not valida.any():
                continue
            rgb, com_dado = ingestao.ler_rgb(ds, janela, escala)
            valida &= com_dado
            dets = detector.detectar(rgb, gsd, valida)
            boas, folgas, emendas = _inteiras(dets, janela, ds.width, ds.height, sobre, margem)
            itens += geometria.vetorizar(boas, tf, folgas, emendas)
            avisar(0.85 * (i + 1) / len(todas), f"Detectando: janela {i + 1} de {len(todas)}")
        avisar(0.88, "Medindo buracos")
        unicos = geometria.deduplicar(itens, t["duplicata_min"])
        defeitos = geometria.medir(unicos, ds.crs, gsd, params["severidade"])
        cortadas = int(defeitos["na_emenda"].sum()) if len(defeitos) else 0
        if cortadas:
            warnings.warn(
                f"{cortadas} deteccao(oes) grandes ficaram na emenda das janelas e podem estar "
                f"truncadas (so objetos de ate {(sobre - 2 * margem) * gsd:.1f} m tem garantia); "
                "aumente tiles.sobreposicao_min_m",
                stacklevel=2,
            )
        seg = params["segmentos"]
        segmentos = pista_mod.segmentar(pista_gdf, float(seg["comprimento_m"]), seg["resto_min_m"])
        defeitos = indicadores.atribuir_segmento(defeitos, segmentos)
        segmentos = indicadores.por_segmento(defeitos, segmentos)

        avisar(0.92, "Gravando previa e recortes")
        limites = exportar.previa(
            orto, saida, int(params["previa"]["largura_max_px"]), params["moldura"], escala
        )
        exportar.recortes(ds, defeitos, saida, params["recortes"], escala)

        resumo = indicadores.resumo(
            defeitos,
            segmentos,
            gsd_m=round(gsd, 6),
            detector=detector.nome,
            versao=detector.versao,
            limites_detector=getattr(detector, "limites", ""),
            previa_limites=limites,
            src=ds.crs.to_string(),
            ortofoto=orto.name,
            largura_px=int(ds.width),
            altura_px=int(ds.height),
            cortadas_por_janela=cortadas,
            processado_em=datetime.now().isoformat(timespec="seconds"),
            fonte=_fonte(orto),
        )
    exportar.gravar(saida, defeitos, segmentos, resumo)
    avisar(1.0, "Pronto")
    return saida
