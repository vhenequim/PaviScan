"""Identidade visual: logos, paleta verde-oliva (tema claro e escuro) e a onca de carregamento.

Sem streamlit: so monta texto (HTML e CSS). Os arquivos de imagem ficam nesta pasta:
  exercito.png  simbolo do Exercito Brasileiro
  gpte2.svg     distintivo do 2o Gpt E, redesenhado em vetor a partir de uma imagem pequena
  onca.svg      silhueta usada no indicador de carregamento. Opcional: sem o arquivo, fica so o anel

As cores de fundo e de texto de cada tema estao em `.streamlit/config.toml`; aqui ficam as dos
elementos proprios do painel. `light-dark()` escolhe a cor pelo tema ativo sem depender do Python.
"""

from __future__ import annotations

import base64
import html
from functools import lru_cache
from pathlib import Path

PASTA = Path(__file__).parent
OURO = "#C9A227"  # o dourado do simbolo do Exercito
OLIVA = "#7D8F3F"  # oliva medio: le bem sobre fundo claro e sobre fundo escuro (graficos)
OLIVA_ESCURO = "#2F381C"
FAIXA = "linear-gradient(100deg, #232B14 0%, #3F4B22 55%, #5F6F30 100%)"
TIPOS = {".png": "image/png", ".svg": "image/svg+xml"}


@lru_cache(maxsize=8)
def data_url(nome: str) -> str | None:
    """Arquivo desta pasta como data URL, ou None se nao existir."""
    arq = PASTA / nome
    if not arq.is_file():
        return None
    return f"data:{TIPOS[arq.suffix]};base64,{base64.b64encode(arq.read_bytes()).decode()}"


def img(nome: str, texto: str) -> str:
    """Tag <img> com o arquivo embutido; vazio se o arquivo nao existir."""
    url = data_url(nome)
    return f'<img src="{url}" alt="{texto}">' if url else ""


def faixa(titulo: str, detalhe_html: str = "") -> str:
    """Faixa de titulo: simbolo do Exercito a esquerda, distintivo do 2o Gpt E a direita.

    `titulo` e escapado aqui. `detalhe_html` entra como esta: quem chama escapa o que vier de fora.
    """
    return (
        "<div class='faixa'>"
        + img("exercito.png", "Exército Brasileiro")
        + f"<div class='faixa-texto'><h1>{html.escape(titulo)}</h1><p>{detalhe_html}</p></div>"
        + img("gpte2.svg", "2º Grupamento de Engenharia")
        + "</div>"
    )


def css() -> str:
    """Bloco <style> do painel: cores dos dois temas, faixa de titulo e onca de carregamento."""
    onca = data_url("onca.svg")
    mascara = f'url("{onca}") center / 66% no-repeat' if onca else "none"
    return f"""
<style>
  [data-testid="stApp"] {{
    --oliva: light-dark(#4F5D2A, #A9BA6B);
    --cartao: light-dark(#ECEEDF, #232A17);
    --texto-fraco: light-dark(#5A6248, #A9B093);
    --selo-fundo: light-dark(#FFFFFF, #1B2112);
    --selo-anel: light-dark(#D3D8BF, #3B4527);
    --onca: light-dark(#2F381C, #DDE5BF);
  }}
  .block-container {{padding-top: 3.2rem; padding-bottom: 2rem; max-width: 1500px;}}
  [data-testid="stMetric"] {{background: var(--cartao); border-radius: 12px; padding: 14px 16px;
                             border-left: 5px solid var(--oliva);}}
  [data-testid="stMetricValue"] {{font-size: 1.8rem;}}
  .faixa {{display: flex; align-items: center; gap: 22px; background: {FAIXA}; color: #fff;
           border-radius: 14px; padding: 14px 24px; margin-bottom: 14px;
           border-bottom: 3px solid {OURO}; box-shadow: 0 2px 10px rgba(0, 0, 0, .18);}}
  .faixa img {{height: 86px; width: auto; flex: none;
               filter: drop-shadow(0 2px 3px rgba(0, 0, 0, .45));}}
  .faixa-texto {{flex: 1; min-width: 0;}}
  .faixa h1 {{color: #fff; margin: 0; font-size: 2.1rem; padding: 0;}}
  .faixa p {{margin: 6px 0 0; opacity: .9; font-size: .92rem;}}
  @media (max-width: 760px) {{
    .faixa {{gap: 12px; padding: 12px 14px;}}
    .faixa img {{height: 54px;}}
    .faixa h1 {{font-size: 1.35rem;}}
  }}
  .selo {{display: inline-block; color: #fff; font-weight: 700; border-radius: 7px;
          padding: 2px 12px;}}
  .cartao {{background: var(--cartao); border-radius: 12px; padding: 12px 14px; height: 100%;}}
  .cartao small {{color: var(--texto-fraco);}}
  .passo {{font-weight: 700; color: var(--oliva); font-size: 1.05rem; margin: 6px 0 2px;}}

  /* carregando: a onca parada dentro de um anel que gira, no canto de baixo da tela */
  @keyframes onca-gira {{to {{transform: rotate(360deg);}}}}
  @keyframes onca-surge {{to {{opacity: 1;}}}}
  @keyframes onca-respira {{0%, 100% {{transform: scale(.93);}} 50% {{transform: scale(1.05);}}}}
  [data-testid="stApp"]::before, [data-testid="stApp"]::after {{
    content: ""; position: fixed; right: 28px; bottom: 28px; width: 84px; height: 84px;
    border-radius: 50%; box-sizing: border-box; opacity: 0; pointer-events: none; z-index: 999990;
  }}
  [data-testid="stApp"]::before {{
    background: var(--selo-fundo); border: 5px solid var(--selo-anel);
    border-top-color: var(--oliva); box-shadow: 0 6px 18px rgba(0, 0, 0, .28);
  }}
  [data-testid="stApp"]::after {{
    background: var(--onca); -webkit-mask: {mascara}; mask: {mascara};
  }}
  /* so aparece se o trabalho passar de meio segundo: clique rapido nao pisca */
  [data-testid="stApp"][data-test-script-state="running"]::before {{
    animation: onca-surge .2s ease .5s forwards, onca-gira .9s linear infinite;
  }}
  [data-testid="stApp"][data-test-script-state="running"]::after {{
    animation: onca-surge .2s ease .5s forwards, onca-respira 1.8s ease-in-out infinite;
  }}
  [data-testid="stStatusWidget"] {{visibility: hidden;}}
  /* a mesma onca no lugar da rodinha dos avisos "Preparando..." */
  [data-testid="stSpinner"] i, [data-testid="stSpinnerIcon"] {{
    width: 2rem !important; height: 2rem !important; border: 0 !important;
    border-radius: 0 !important; background: var(--onca) !important;
    -webkit-mask: {mascara.replace("66%", "contain")}; mask: {mascara.replace("66%", "contain")};
    animation: onca-respira 1.2s ease-in-out infinite !important;
  }}
</style>
"""
