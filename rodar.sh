#!/usr/bin/env bash
# Sobe o painel do PaviScan com um comando so.
#   ./rodar.sh                  painel em http://localhost:8501
#   ./rodar.sh --porta 8502     outra porta
#   ./rodar.sh --runs pasta/    outra pasta de levantamentos
# Na primeira vez cria o ambiente Python em .venv e instala as dependencias.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Primeira execucao: criando o ambiente Python em .venv ..."
  if command -v uv >/dev/null 2>&1; then
    uv venv --python 3.12 .venv
  else
    py=""
    for candidato in python3.12 python3.13 python3.11 python3.10; do
      if command -v "$candidato" >/dev/null 2>&1; then
        py="$candidato"
        break
      fi
    done
    if [ -z "$py" ]; then
      echo "Nao achei o uv nem Python 3.10 a 3.13." >&2
      echo "Instale o uv (https://docs.astral.sh/uv/) ou o Python 3.12 e rode de novo." >&2
      exit 1
    fi
    "$py" -m venv .venv
  fi
fi

if [ ! -x .venv/bin/paviscan ]; then
  echo "Instalando as dependencias (leva alguns minutos e precisa de internet) ..."
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python .venv/bin/python -e ".[dev]"
  else
    .venv/bin/python -m pip install -e ".[dev]"
  fi
fi

exec .venv/bin/paviscan painel "$@"
