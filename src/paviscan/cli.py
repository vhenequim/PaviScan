"""Linha de comando: paviscan run | pista | oam | painel | sintetico."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import typer

app = typer.Typer(add_completion=False, help="PaviScan: buracos em pavimento a partir de ortofoto.")


@app.command()
def run(
    orto: Path = typer.Option(..., exists=True, dir_okay=False, help="Ortofoto GeoTIFF"),
    saida: Path = typer.Option(..., help="Pasta do levantamento, ex.: runs/nome"),
    pista: Path | None = typer.Option(
        None, help="Poligono da pista (GeoJSON). Sem ele, gera pelas vias do OpenStreetMap"
    ),
    eixo: Path | None = typer.Option(
        None, help="Eixo da via desenhado (GeoJSON de linhas), em vez do OpenStreetMap"
    ),
    superficie: str = typer.Option("asfalto", help="asfalto | terra (via gasta ou de terra)"),
    config: Path | None = typer.Option(None, help="params.yaml alternativo"),
) -> None:
    """Processa uma ortofoto e grava o levantamento em --saida."""
    from . import pipeline, pista_osm

    if pista is None:
        pista = orto.parent / "pista.geojson"
        if not pista.is_file():
            typer.echo("Sem --pista: gerando a pista pelos eixos de via...")
            pista_osm.gerar(orto, pista, config, eixo=eixo, superficie=superficie)
        typer.echo(f"Pista: {pista}")

    with typer.progressbar(length=100, label="Processando") as barra:
        feito = 0

        def avisar(fracao: float, _texto: str) -> None:
            nonlocal feito
            alvo = int(100 * fracao)
            barra.update(alvo - feito)
            feito = alvo

        pipeline.processar(orto, pista, saida, config, progresso=avisar)
    typer.echo(f"Levantamento gravado em {saida}")


@app.command("pista")
def pista_cmd(
    orto: Path = typer.Option(..., exists=True, dir_okay=False, help="Ortofoto GeoTIFF"),
    saida: Path = typer.Option(..., help="GeoJSON de saida, ex.: dados/x/pista.geojson"),
    vias: Path | None = typer.Option(None, help="JSON do Overpass ja baixado (uso offline)"),
    eixo: Path | None = typer.Option(
        None, help="Eixo da via desenhado (GeoJSON de linhas), em vez do OpenStreetMap"
    ),
    superficie: str = typer.Option("asfalto", help="asfalto | terra (via gasta ou de terra)"),
    config: Path | None = typer.Option(None, help="params.yaml alternativo"),
) -> None:
    """Gera o poligono da pista pelas vias do OpenStreetMap e pela cor do asfalto."""
    from . import pista_osm

    destino = pista_osm.gerar(orto, saida, config, vias, eixo, superficie)
    typer.echo(f"Pista gravada em {destino}")


@app.command()
def oam(
    ref: str = typer.Argument(..., help="Link do imagery.hotosm.org (com selected_id) ou o id"),
    pasta: Path = typer.Argument(..., help="Pasta de destino, ex.: dados/nome"),
) -> None:
    """Baixa uma ortofoto do OpenAerialMap com o credito e a licenca."""
    from . import oam as oam_mod

    typer.echo(f"Ortofoto gravada em {oam_mod.baixar(ref, pasta)}")


@app.command()
def painel(
    runs: Path = typer.Option(Path("runs"), help="Pasta com um levantamento por subpasta"),
    porta: int = typer.Option(8501, help="Porta do servidor"),
) -> None:
    """Abre o dashboard."""
    alvo = Path(__file__).parent / "painel" / "app.py"
    ambiente = {**os.environ, "PAVISCAN_RUNS": str(runs.resolve())}
    comando = [sys.executable, "-m", "streamlit", "run", str(alvo)]
    # headless + abrir o navegador aqui: evita a pergunta de e-mail da primeira execucao
    comando += ["--server.port", str(porta), "--server.headless", "true"]
    comando += ["--browser.gatherUsageStats", "false"]
    threading.Timer(2.5, webbrowser.open, args=(f"http://localhost:{porta}",)).start()
    raise typer.Exit(subprocess.call(comando, env=ambiente, cwd=Path(__file__).parents[2]))


@app.command()
def sintetico(
    pasta: Path = typer.Argument(Path("dados/sint/cena"), help="Pasta de destino"),
    seed: int = typer.Option(7, help="Semente"),
    buracos: int = typer.Option(8, help="Numero de buracos"),
) -> None:
    """Gera uma cena sintetica com gabarito (ortofoto, pista e buracos conhecidos)."""
    from . import sintetico as sint

    typer.echo(f"Cena gravada em {sint.gerar_cena(pasta, n_buracos=buracos, seed=seed)}")


if __name__ == "__main__":
    app()
