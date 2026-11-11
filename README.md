# PaviScan

Buracos em pavimento a partir de ortofotos de drone: localização, medidas e um dashboard com filtros. Retrato de uma imagem só, tudo em Python. Plano em `PLAN.md`, estado em `PROGRESS.md`.

## Instalar

```bash
python3.12 -m venv .venv && source .venv/bin/activate   # ou: uv venv --python 3.12 .venv
pip install -e ".[dev]"
```

Use Python 3.10 a 3.13 (as rodas do rasterio e do OpenCV ainda não cobrem versões mais novas).

## Demo em três comandos

```bash
paviscan oam "https://imagery.hotosm.org/browse?lat=-3.0042&lon=-60.0445&zoom=16.4&selected_id=6a7f68e477ca9598cc9fe89b" dados/taruma
paviscan run --orto dados/taruma/orto.tif --saida runs/taruma
paviscan painel --runs runs/
```

1. `oam` baixa a ortofoto do OpenAerialMap (GeoTIFF) e guarda o crédito e a licença em `stac_item.json`.
2. `run` processa a imagem. Sem `--pista`, a pista é gerada sozinha: eixos de via do OpenStreetMap + cor do asfalto, gravada em `pista.geojson` ao lado da ortofoto. Essa consulta envia o retângulo geográfico da imagem ao servidor público do OpenStreetMap (Overpass); com `--pista` nada sai da máquina. Leva cerca de 30 s por imagem de 300 megapixels.
3. `painel` abre o dashboard no navegador.

As duas imagens de trabalho (Manaus, drone Phantom 4, CC-BY 4.0, Perito Paulo Rafael via OpenAerialMap):

| Pasta | Bairro | GSD | `selected_id` |
|---|---|---|---|
| `dados/taruma` | Tarumã | 3,0 cm/px | `6a7f68e477ca9598cc9fe89b` |
| `dados/novo_aleixo` | Novo Aleixo | 1,8 cm/px | `6a7f6f8d77ca9598cc9ff5d2` |

Sem internet, a cena sintética serve de plano B:

```bash
paviscan sintetico dados/sint/cena
paviscan run --orto dados/sint/cena/orto.tif --pista dados/sint/cena/pista.geojson --saida runs/sintetico
```

## Comandos

| Comando | O que faz |
|---|---|
| `paviscan run --orto X.tif [--pista P.geojson] --saida runs/nome` | Pipeline completo |
| `paviscan pista --orto X.tif --saida P.geojson` | Só a pista automática (OpenStreetMap + cor do asfalto) |
| `paviscan oam <link ou id> <pasta>` | Baixa uma ortofoto do OpenAerialMap |
| `paviscan painel --runs runs/` | Dashboard |
| `paviscan sintetico <pasta> --seed 7` | Cena sintética com gabarito |
| `./scripts/verify.sh` | Lint + testes unitários + golden. É a definição de pronto |

## O que sai de um levantamento (`runs/nome/`)

| Arquivo | Conteúdo |
|---|---|
| `defeitos.geojson` | Um polígono por buraco, em UTM: `id`, `area_m2`, `largura_m`, `comprimento_m`, `confianca`, `severidade`, `segmento`, `via`, `na_emenda` |
| `segmentos.geojson` | Trechos de 20 m, só onde a ortofoto tem dado: `area_pista_m2`, `n_buracos`, `area_buracos_m2`, `pct_afetada` |
| `resumo.json` | Totais, GSD, detector, limites da prévia, crédito da imagem |
| `previa.png` | Ortofoto reduzida para o mapa do dashboard |
| `recortes/<id>.jpg` | Miniatura em resolução cheia de cada buraco, com o contorno |
| `defeitos.gpkg`, `resumo.xlsx` | As mesmas tabelas para QGIS e Excel |

## Como o detector decide

Anomalia escura local: cada pixel é comparado com o asfalto em volta (mediana numa janela de 2 m), o limiar é robusto (`max(piso, mediana + k x MAD)`, não Otsu) e as regiões passam por filtros de forma em metros (área, solidez, alongamento). A confiança junta contraste, nitidez da borda e forma compacta. Os limiares do detector, da pista, dos segmentos e das janelas estão em `config/params.yaml` (o projeto precisa estar instalado com `pip install -e .` para esse arquivo ser encontrado).

Na cena sintética (sementes 7, 11, 23, 42; GSD de 2, 3 e 5 cm): recall de 100 %, zero falso positivo, erro mediano de área de 3 a 5 %, zero detecção em pista limpa.

## Limites, para dizer na apresentação

- Não há aprendizado. Mancha de óleo, remendo escuro, sarjeta, trinca larga, sombra pequena, moto e animal aparecem como buraco. Nas duas imagens reais a maior parte dos candidatos é isso; os de confiança alta no Tarumã são, na maioria, buraco ou remendo de verdade. O filtro de confiança e a conferência visual do painel existem para essa triagem.
- A pista automática depende do OpenStreetMap estar alinhado com a ortofoto e de a via ser de asfalto cinza. Um polígono desenhado no QGIS (`--pista`) continua sendo o caminho mais confiável para um trecho específico.
- Severidade por área, em três faixas de trabalho. Não é classificação de norma.
- O detector serve para defeitos de até cerca de 2 m. A imagem é processada em janelas que se sobrepõem 4 m; a menos de 1 m da borda de uma janela o detector perde contexto, então a detecção ali fica para a janela vizinha. Defeito de até 2 m sai sempre inteiro de alguma janela. Acima disso ele pode sair truncado ou dividido: em geral fica a maior versão vista, marcada na coluna `na_emenda`, com aviso no processamento, no painel e em `cortadas_por_janela` do `resumo.json`. A marca não cobre tudo: objeto com mais de 2 m nas duas dimensões, objeto muito longo (6 m) e defeito grande que o detector divide em partes podem sair errados ou pela metade sem marca nenhuma.
- O resultado depende um pouco da grade de janelas, porque o limiar e o fundo são calculados por janela. Trocando o tamanho da janela nas duas imagens reais, os totais mudaram de 1 a 5 % e cerca de 12 % dos candidatos fracos entraram ou saíram; entre os de confiança >= 0,8 a troca foi de 1 a 3 candidatos. A regra da emenda também deixa de fora 1 a 2 % de candidatos reais, e duas detecções vizinhas podem sair como uma só quando outra janela as vê fundidas.
- Dois buracos colados só são separados quando têm tamanho parecido; um grande colado a um pequeno é tratado como uma região só (e pode cair no filtro de forma).
- A pista é segmentada pelo eixo de cada trecho reto. Via muito curva deve ser desenhada em mais de um trecho.
- Fora do escopo: orçamento, comparação entre datas, profundidade, trincas.

## Créditos dos dados

Ortofotos: Perito Paulo Rafael, CC-BY 4.0, via OpenAerialMap. Vias: © colaboradores do OpenStreetMap, ODbL.
