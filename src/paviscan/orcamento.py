"""Quantitativos e orcamento de reparo. As regras de engenharia ficam nas tabelas de config/.

Tres solucoes, descritas em config/solucoes.csv:
  A  tapa-buraco superficial, por defeito;
  B  remendo profundo, por defeito;
  C  fresagem e recapeamento do segmento inteiro, quando a area de corte passa de uma
     fracao do segmento.
O "plano recomendado" escolhe uma solucao por defeito e troca por C nos segmentos criticos.
Os precos vem de config/precos.csv. Linha com fonte PLACEHOLDER e ilustrativa: o codigo
nunca inventa nem corrige preco.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd

COLUNAS = ["id", "opcao", "servico", "unidade", "quantidade", "preco_unit", "custo"]
PAVIMENTOS = ["CBUQ", "PMF", "TSD"]
TERRENOS = ["bom", "regular", "ruim"]


def _pasta_config(config: Path | None = None) -> Path:
    from . import config as config_mod

    return config_mod.localizar(config).parent


def carregar_tabelas(config: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(solucoes, precos) lidas de config/. `precos` ganha a coluna bool `placeholder`."""
    pasta = _pasta_config(config)
    solucoes = pd.read_csv(pasta / "solucoes.csv")
    precos = pd.read_csv(pasta / "precos.csv", dtype={"fonte": str, "data_base": str})
    precos["fonte"] = precos["fonte"].fillna("")
    precos["data_base"] = precos["data_base"].fillna("")
    precos["placeholder"] = precos["fonte"].str.upper().eq("PLACEHOLDER") | precos["fonte"].eq("")
    return solucoes, precos


def area_corte(defeitos: gpd.GeoDataFrame, cfg: dict) -> pd.Series:
    """Area a cortar em cada defeito.

    Buraco: retangulo minimo com `margem_corte_m` de folga em volta. Classe extensa
    (`corte_pela_area`): a propria area vezes `folga_area`, porque o retangulo de uma
    faixa longa e torta seria muito maior que o dano.
    """
    m = float(cfg["margem_corte_m"])
    ret = (defeitos["largura_m"] + 2 * m) * (defeitos["comprimento_m"] + 2 * m)
    pela_area = defeitos["classe"].isin(cfg.get("corte_pela_area", []))
    return ret.where(~pela_area, defeitos["area_m2"] * float(cfg["folga_area"]))


def linhas_da_solucao(
    solucoes: pd.DataFrame, opcao: str, pavimento: str, terreno: str
) -> pd.DataFrame:
    """Servicos de uma opcao para o pavimento e o terreno dados.

    `*` casa com tudo. Quando ha linha especifica e linha `*` para o mesmo servico, vale
    a especifica.
    """
    s = solucoes[solucoes["opcao"] == opcao]
    s = s[s["pavimento"].isin(["*", pavimento]) & s["terreno"].isin(["*", terreno])].copy()
    s["especifica"] = (s["pavimento"] != "*").astype(int) + (s["terreno"] != "*").astype(int)
    s = s.sort_values("especifica", ascending=False).drop_duplicates("servico")
    return s.sort_index()


def _itens(
    ident, opcao: str, base: dict[str, float], linhas: pd.DataFrame, preco: dict[str, float]
) -> list[dict]:
    saida = []
    for linha in linhas.itertuples():
        if linha.servico not in preco:
            raise KeyError(f"servico sem preco em config/precos.csv: {linha.servico}")
        quantidade = base[linha.base_quantidade] * float(linha.fator)
        saida.append(
            {
                "id": ident,
                "opcao": opcao,
                "servico": linha.servico,
                "unidade": linha.unidade,
                "quantidade": quantidade,
                "preco_unit": preco[linha.servico],
                "custo": quantidade * preco[linha.servico],
            }
        )
    return saida


def orcar(
    defeitos: gpd.GeoDataFrame,
    segmentos: gpd.GeoDataFrame,
    solucoes: pd.DataFrame,
    precos: pd.DataFrame,
    cfg: dict,
) -> pd.DataFrame:
    """Uma linha por (defeito ou segmento, opcao, servico). custo = quantidade x preco_unit.

    Opcao C aparece so nos segmentos em que a soma das areas de corte, dividida pela area
    do segmento, passa de `limite_recapeamento`; nela o `id` e `seg-N`. Sem BDI: o BDI entra
    so no total (ver `totais`).
    """
    preco = dict(zip(precos["servico"], precos["preco_unit"].astype(float), strict=True))
    pavimento, terreno = cfg["pavimento"], cfg["terreno"]
    itens: list[dict] = []
    if len(defeitos):
        corte = area_corte(defeitos, cfg)
        for opcao in ("A", "B"):
            linhas = linhas_da_solucao(solucoes, opcao, pavimento, terreno)
            for ident, a in zip(defeitos["id"], corte, strict=True):
                itens += _itens(int(ident), opcao, {"area_corte": float(a)}, linhas, preco)
        linhas_c = linhas_da_solucao(solucoes, "C", pavimento, terreno)
        por_seg = corte.groupby(defeitos["segmento"]).sum()
        for seg in segmentos.itertuples():
            fracao = float(por_seg.get(seg.segmento, 0.0)) / seg.area_pista_m2
            if fracao > cfg["limite_recapeamento"]:
                base = {"area_segmento": float(seg.area_pista_m2)}
                itens += _itens(f"seg-{int(seg.segmento)}", "C", base, linhas_c, preco)
    return pd.DataFrame(itens, columns=COLUNAS)


def plano_recomendado(
    defeitos: gpd.GeoDataFrame, orc: pd.DataFrame, cfg: dict
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Escolhe uma solucao por defeito e devolve (itens do plano, tabela defeito -> solucao).

    Regra, em `orcamento.plano` do params.yaml: cada classe e severidade apontam para A ou B;
    nos segmentos com opcao C, o recapeamento substitui os reparos individuais do segmento.
    """
    plano = cfg["plano"]
    seg_c = {int(str(i).removeprefix("seg-")) for i in orc.loc[orc["opcao"] == "C", "id"]}
    escolha = []
    for d in defeitos.itertuples():
        if int(d.segmento) in seg_c:
            opcao = "C"
        else:
            por_classe = plano.get(d.classe, plano["panela"])
            opcao = por_classe[d.severidade]
        escolha.append({"id": int(d.id), "solucao": opcao})
    escolha = pd.DataFrame(escolha, columns=["id", "solucao"])
    chave = escolha[escolha["solucao"] != "C"].rename(columns={"solucao": "opcao"})
    individuais = orc[orc["opcao"].isin(["A", "B"])].merge(chave, on=["id", "opcao"])
    itens = pd.concat([individuais, orc[orc["opcao"] == "C"]], ignore_index=True)
    return itens[COLUNAS], escolha


def trechos_a_recapear(orc: pd.DataFrame) -> int:
    """Numero de segmentos que receberam a opcao C."""
    return int(orc.loc[orc["opcao"] == "C", "id"].nunique())


def totais(orc: pd.DataFrame, bdi: float) -> dict[str, dict[str, float]]:
    """Por opcao: custo direto, BDI (so no total) e total."""
    saida = {}
    for opcao, grupo in orc.groupby("opcao"):
        direto = float(grupo["custo"].sum())
        saida[str(opcao)] = {"direto": direto, "bdi": direto * bdi, "total": direto * (1 + bdi)}
    return saida


def total(itens: pd.DataFrame, bdi: float) -> dict[str, float]:
    """Custo direto, BDI e total de um conjunto de itens (por exemplo, o plano recomendado)."""
    direto = float(itens["custo"].sum()) if len(itens) else 0.0
    return {"direto": direto, "bdi": direto * bdi, "total": direto * (1 + bdi)}


def por_servico(itens: pd.DataFrame) -> pd.DataFrame:
    """Quantitativos agregados por servico: quantidade, preco unitario e custo."""
    if itens.empty:
        return pd.DataFrame(columns=["servico", "unidade", "quantidade", "preco_unit", "custo"])
    g = itens.groupby(["servico", "unidade", "preco_unit"], as_index=False)[
        ["quantidade", "custo"]
    ].sum()
    return g.sort_values("custo", ascending=False).reset_index(drop=True)[
        ["servico", "unidade", "quantidade", "preco_unit", "custo"]
    ]


def por_via(itens: pd.DataFrame, defeitos: gpd.GeoDataFrame, segmentos: gpd.GeoDataFrame):
    """Custo direto do conjunto de itens por via."""
    if itens.empty:
        return pd.DataFrame(columns=["via", "custo"])
    via_do_defeito = dict(zip(defeitos["id"].astype(int), defeitos["via"], strict=True))
    via_do_seg = dict(zip(segmentos["segmento"].astype(int), segmentos["via"], strict=True))

    def via(ident) -> str:
        if isinstance(ident, str) and ident.startswith("seg-"):
            return via_do_seg.get(int(ident[4:]), "")
        return via_do_defeito.get(int(ident), "")

    t = itens.assign(via=itens["id"].map(via)).groupby("via", as_index=False)["custo"].sum()
    return t.sort_values("custo", ascending=False).reset_index(drop=True)


def gravar(
    saida: Path, orc: pd.DataFrame, plano: pd.DataFrame, escolha: pd.DataFrame, precos, bdi: float
) -> dict:
    """orcamento.csv e orcamento.xlsx (Resumo, Plano, Por defeito, Precos). Devolve o resumo."""
    orc.to_csv(saida / "orcamento.csv", index=False)
    resumo = {
        "custo_por_opcao": {k: round(v["direto"], 2) for k, v in totais(orc, bdi).items()},
        "custo_plano": {k: round(v, 2) for k, v in total(plano, bdi).items()},
        "bdi": bdi,
        "precos_placeholder": bool(precos["placeholder"].any()),
    }
    linhas = [("BDI", bdi)]
    for opcao, v in totais(orc, bdi).items():
        linhas += [
            (f"Opcao {opcao}: custo direto", v["direto"]),
            (f"Opcao {opcao}: total", v["total"]),
        ]
    linhas += [(f"Plano recomendado: {k}", v) for k, v in total(plano, bdi).items()]
    if resumo["precos_placeholder"]:
        linhas.append(("AVISO", "ha precos PLACEHOLDER (ilustrativos) em config/precos.csv"))
    with pd.ExcelWriter(saida / "orcamento.xlsx", engine="openpyxl") as xl:
        pd.DataFrame(linhas, columns=["item", "valor"]).to_excel(
            xl, sheet_name="Resumo", index=False
        )
        por_servico(plano).to_excel(xl, sheet_name="Plano recomendado", index=False)
        orc.merge(escolha, on="id", how="left").to_excel(xl, sheet_name="Por defeito", index=False)
        precos.to_excel(xl, sheet_name="Precos", index=False)
    return resumo
