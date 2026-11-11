# PaviScan

Condição do pavimento a partir de ortofoto de drone: a imagem entra, os defeitos saem localizados e medidos, e um painel mostra a condição por via, os trechos prioritários, a estimativa de reparo e um relatório pronto para imprimir. Retrato de uma imagem só, tudo em Python. Plano em `PLAN.md`, estado em `PROGRESS.md`.

## Instalar

```bash
python3.12 -m venv .venv && source .venv/bin/activate   # ou: uv venv --python 3.12 .venv
pip install -e ".[dev]"
```

Use Python 3.10 a 3.13 (as rodas do rasterio e do OpenCV ainda não cobrem versões mais novas). A instalação precisa ser editável (`-e`): é assim que `config/` é encontrado.

## Usar pelo painel

```bash
paviscan painel --runs runs/
```

Na aba **Nova análise**:

1. **Imagem.** Envie o GeoTIFF (até 4 GB) ou aponte o caminho do arquivo no disco. A imagem é conferida e guardada no acervo (`dados/<nome>/orto.tif`) e pode ser reprocessada depois.
2. **Onde está a via.** Três caminhos: automático pelas ruas do OpenStreetMap; desenhar o eixo da via em cima da imagem; ou enviar um arquivo (polígono da pista ou linha do eixo). Escolha a superfície: asfalto de cor normal, ou via muito gasta ou de terra.
3. **Processar.** Leva de 30 s a alguns minutos, conforme o tamanho e o formato da imagem.

O modo automático envia o retângulo geográfico da imagem ao servidor público do OpenStreetMap. Nos outros dois nada sai da máquina.

As demais abas:

| Aba | O que mostra |
|---|---|
| Visão geral | Condição geral (conceito A a E), defeitos, área, % afetada e custo de reparo; mapa com a ortofoto, condição por trecho e relevo 3D; trechos prioritários com foto |
| Vias e trechos | Conceito e índice por via, perfil ao longo da pista, piores segmentos |
| Inspeção | Recorte em resolução cheia de cada defeito, com o contorno detectado |
| Orçamento | Plano recomendado e alternativas, por via e por serviço, com preços, BDI, revestimento e terreno ajustáveis |
| Dados e relatório | Relatório executivo em HTML (imprime em PDF), tabela e downloads CSV, XLSX e GeoJSON |
| Próximos passos | O caminho para o modelo treinado, com a base pública UAV-PDD2023 |

Os filtros da barra lateral (tipo de defeito, área, severidade, confiança, via, segmento) valem para todas as abas, inclusive orçamento e relatório.

## Usar pela linha de comando

| Comando | O que faz |
|---|---|
| `paviscan run --orto X.tif --saida runs/nome` | Pipeline completo, com a pista pelo OpenStreetMap |
| `paviscan run --orto X.tif --pista P.geojson --saida runs/nome` | Com o polígono da pista desenhado por você |
| `paviscan run --orto X.tif --eixo E.geojson --superficie terra --saida runs/nome` | Com o eixo da via desenhado (via gasta ou de terra) |
| `paviscan pista --orto X.tif --saida P.geojson [--eixo E.geojson] [--superficie terra]` | Só gera a pista |
| `paviscan oam <link ou id> <pasta>` | Baixa uma ortofoto do OpenAerialMap |
| `paviscan sintetico <pasta> --seed 7` | Cena sintética com gabarito |
| `./scripts/verify.sh` | Lint + testes unitários + golden. É a definição de pronto |

Imagens de trabalho:

| Pasta | Local | GSD | Origem |
|---|---|---|---|
| `dados/querari` | Querari (AM), via de asfalto muito gasto | 2,7 cm | ortofoto própria, em graus e com banda alfa; eixo desenhado em `eixo.geojson` |
| `dados/taruma` | Tarumã, Manaus | 3,0 cm | OpenAerialMap `6a7f68e477ca9598cc9fe89b`, CC-BY 4.0 |
| `dados/novo_aleixo` | Novo Aleixo, Manaus | 1,8 cm | OpenAerialMap `6a7f6f8d77ca9598cc9ff5d2`, CC-BY 4.0 |
| `dados/sint/cena` | cena sintética | 2,0 cm | `paviscan sintetico` |

## O que sai de um levantamento (`runs/nome/`)

| Arquivo | Conteúdo |
|---|---|
| `defeitos.geojson` | Um polígono por defeito, em UTM: `id`, `classe`, `area_m2`, `largura_m`, `comprimento_m`, `confianca`, `severidade`, `segmento`, `via`, `na_emenda` |
| `segmentos.geojson` | Trechos de 20 m, só onde a ortofoto tem dado: `area_pista_m2`, `n_buracos`, `area_buracos_m2`, `pct_afetada` |
| `orcamento.csv`, `orcamento.xlsx` | Quantitativos e custo por defeito e por opção de reparo |
| `resumo.json` | Totais, GSD, detector, custos, limites da prévia, crédito da imagem |
| `previa.png`, `recortes/<id>.jpg` | Ortofoto reduzida para o mapa e miniatura em resolução cheia de cada defeito |
| `defeitos.gpkg`, `resumo.xlsx` | As mesmas tabelas para QGIS e Excel |

## Como o detector decide

Sem aprendizado: cada ponto é comparado com o pavimento em volta, com limiar robusto e medidas em metros. São duas passadas.

- **Buraco** (`panela`): anomalia escura contra a mediana de uma janela de 2 m, com filtros de forma (área, solidez, alongamento).
- **Revestimento perdido** (`solo_exposto`): área mais clara e de cor de solo (do vermelho ao laranja) contra o pavimento mais escuro de uma janela de 6 m. Mede área, sem filtro de forma, porque esse dano forma faixas longas; os pedaços são unidos entre janelas e repartidos pelos segmentos.

A confiança junta contraste e nitidez da borda (e forma compacta, no buraco). Todos os limiares estão em `config/params.yaml`.

Na cena sintética (sementes 7, 11, 23, 42; GSD de 2, 3 e 5 cm): recall de 100 %, zero falso positivo, erro mediano de área de 3 a 5 %, zero detecção em pista limpa.

## Condição e orçamento

- **Índice de condição** (0 a 100) e **conceito** (A a E) por segmento e por via: perde 8 pontos por 1 % de área afetada e 1 ponto por defeito a cada 100 m (`condicao` no `params.yaml`). É um índice de trabalho para ordenar trechos. Não é IGG nem ICP de norma.
- **Orçamento.** Três soluções em `config/solucoes.csv`: A, tapa-buraco superficial; B, remendo profundo; C, fresagem e recapeamento do trecho inteiro, quando a área de corte passa de 15 % do segmento. O plano recomendado usa A para buraco pequeno ou médio, B para buraco grande e para revestimento perdido, e troca por C nos trechos críticos. O BDI entra só no total.
- **Os preços de `config/precos.csv` são ilustrativos** (fonte `PLACEHOLDER`). O painel avisa enquanto houver um deles. Troque pelos de referência (SICRO, SINAPI ou contrato) antes de usar qualquer valor.

## Limites, para dizer na apresentação

- Os números são candidatos a defeito. Mancha de óleo, remendo escuro, sarjeta, sombra pequena e veículo escuro aparecem como buraco; lama, areia, calçada e concreto de cor quente aparecem como revestimento perdido. Nas imagens de Manaus a maior parte dos candidatos é isso. No Querari, onde o dano é extenso e visível, o contorno acompanha bem as áreas sem capa. O filtro de confiança e a aba Inspeção existem para essa triagem.
- O custo herda esses erros e usa preços ilustrativos: serve para mostrar o método, não para fechar contrato.
- O detector serve para buracos de até cerca de 2 m. A imagem é processada em janelas que se sobrepõem 4 m; a menos de 1 m da borda de uma janela o detector perde contexto, então a detecção ali fica para a janela vizinha. Buraco de até 2 m sai sempre inteiro de alguma janela. Acima disso ele pode sair truncado ou dividido: em geral fica a maior versão vista, marcada na coluna `na_emenda`, com aviso no processamento, no painel e em `cortadas_por_janela` do `resumo.json`. A marca não cobre tudo: objeto com mais de 2 m nas duas dimensões, objeto muito longo (6 m) e buraco grande que o detector divide em partes podem sair errados ou pela metade sem marca nenhuma.
- O resultado depende um pouco da grade de janelas, porque o limiar e o fundo são calculados por janela. Trocando o tamanho da janela nas imagens de Manaus, os totais mudaram de 1 a 5 % e cerca de 12 % dos candidatos fracos entraram ou saíram.
- A pista automática depende do OpenStreetMap estar alinhado com a ortofoto. Onde não há rua mapeada (caso do Querari), desenhe o eixo. A pista é segmentada pelo eixo de cada trecho reto.
- Severidade por área, em três faixas de trabalho. Não é classificação de norma.
- Imagem em graus (EPSG:4326) é reprojetada durante a leitura, o que deixa o processamento bem mais lento (cerca de 4 min no Querari, contra 30 s nas imagens já em UTM).
- Fora do escopo: comparação entre datas, profundidade, trincas.

## Créditos dos dados

Ortofotos de Manaus: Perito Paulo Rafael, CC-BY 4.0, via OpenAerialMap. Vias: © colaboradores do OpenStreetMap, ODbL. Base UAV-PDD2023: Yan e Zhang, 2023, Zenodo (https://zenodo.org/records/8429208).
