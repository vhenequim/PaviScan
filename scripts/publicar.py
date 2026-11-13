"""Monta a versao publicada do PaviScan (Streamlit Community Cloud) numa pasta.

Uso:
    python scripts/publicar.py <pasta_destino> [levantamento ...]

A pasta destino e o clone do repositorio PRIVADO de publicacao. Nunca use o repositorio
publico: a versao publicada leva as previas e as miniaturas dos levantamentos.

O que vai para a pasta (tudo que estava nela, menos `.git`, e apagado antes):
- o codigo do commit atual (`git archive HEAD`): o que nao foi commitado nao vai;
- `publicado/runs/<nome>/`: defeitos, segmentos, resumo (sem o caminho local da ortofoto),
  revisao, miniaturas e a previa em WebP (bem menor que o PNG). A ortofoto nao vai;
- `publicado/dados/uavpdd/`: resumo e exemplos da base publica, para "Proximos passos";
- `requirements.txt` com as dependencias do pyproject.toml e `streamlit_app.py`, a entrada
  do app (poe `src/` no caminho e roda o painel; o Community Cloud nao instala o pacote).

Sem nomes, publica todos os levantamentos de runs/ menos a cena sintetica. No Community
Cloud: entrada `streamlit_app.py`, Python 3.12.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import tomllib
from PIL import Image

RAIZ = Path(__file__).resolve().parents[1]
ARQUIVOS_RUN = ("defeitos.geojson", "segmentos.geojson", "revisao.json")
ENTRADA = '''"""Entrada do Streamlit Community Cloud: poe src/ no caminho e roda o painel."""

import runpy
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ / "src"))
runpy.run_path(str(RAIZ / "src" / "paviscan" / "painel" / "app.py"), run_name="__main__")
'''


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=RAIZ, check=True, capture_output=True).stdout


def _limpar(destino: Path) -> None:
    """Apaga tudo na pasta destino, menos o .git (o historico do repositorio privado)."""
    for item in destino.iterdir():
        if item.name == ".git":
            continue
        shutil.rmtree(item) if item.is_dir() and not item.is_symlink() else item.unlink()


def _codigo(destino: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(_git("archive", "--format=tar", "HEAD"))) as tar:
        tar.extractall(destino, filter="data")


def _levantamento(origem: Path, destino: Path) -> int:
    destino.mkdir(parents=True)
    for nome in ARQUIVOS_RUN:
        if (origem / nome).is_file():
            shutil.copy2(origem / nome, destino / nome)
    resumo = json.loads((origem / "resumo.json").read_text())
    resumo.pop("ortofoto_caminho", None)  # caminho do computador de quem processou
    (destino / "resumo.json").write_text(json.dumps(resumo, indent=2, ensure_ascii=False))
    Image.open(origem / "previa.png").convert("RGBA").save(
        destino / "previa.webp", format="WEBP", quality=85, method=6
    )
    if (origem / "recortes").is_dir():
        shutil.copytree(origem / "recortes", destino / "recortes")
    return sum(f.stat().st_size for f in destino.rglob("*") if f.is_file())


def _base_publica(destino: Path) -> None:
    origem = RAIZ / "dados" / "uavpdd"
    if not (origem / "resumo.json").is_file():
        return
    (destino / "Annotations").mkdir(parents=True)  # base_publica.resumo exige a pasta
    (destino / "Annotations" / ".gitkeep").write_text("")
    shutil.copy2(origem / "resumo.json", destino / "resumo.json")
    if (origem / "exemplos").is_dir():
        shutil.copytree(origem / "exemplos", destino / "exemplos")


def _dependencias(destino: Path) -> None:
    proj = tomllib.loads((RAIZ / "pyproject.toml").read_text())["project"]
    linhas = ["# Gerado por scripts/publicar.py a partir do pyproject.toml", *proj["dependencies"]]
    (destino / "requirements.txt").write_text("\n".join(linhas) + "\n")
    (destino / "streamlit_app.py").write_text(ENTRADA)


def publicar(destino: Path, nomes: list[str]) -> None:
    destino = destino.resolve()
    if destino == RAIZ or RAIZ in destino.parents:
        sys.exit("a pasta destino tem que ficar fora do repositorio publico")
    if _git("status", "--porcelain", "--untracked-files=no").strip():
        print("aviso: ha mudancas sem commit; vai so o que esta no ultimo commit")
    destino.mkdir(parents=True, exist_ok=True)
    _limpar(destino)
    _codigo(destino)
    runs = RAIZ / "runs"
    if not nomes:
        nomes = sorted(p.name for p in runs.iterdir() if (p / "resumo.json").is_file())
        nomes = [n for n in nomes if n != "sintetico"]
    for nome in nomes:
        tamanho = _levantamento(runs / nome, destino / "publicado" / "runs" / nome)
        print(f"{nome}: {tamanho / 1e6:.1f} MB")
    _base_publica(destino / "publicado" / "dados" / "uavpdd")
    _dependencias(destino)
    total = sum(
        f.stat().st_size for f in destino.rglob("*") if f.is_file() and ".git" not in f.parts
    )
    commit = _git("rev-parse", "--short", "HEAD").decode().strip()
    print(f"pronto em {destino} ({total / 1e6:.1f} MB, commit {commit})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    publicar(Path(sys.argv[1]), sys.argv[2:])
