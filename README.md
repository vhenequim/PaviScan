# PaviScan

Condição do pavimento a partir de ortofoto de drone: a imagem entra, os defeitos saem localizados e medidos, e um painel mostra a condição por via, os trechos prioritários, a estimativa de reparo e um relatório pronto para imprimir. Retrato de uma imagem só, tudo em Python. Plano em `PLAN.md`, estado em `PROGRESS.md`.

## Rodar (um comando só)

```bash
./rodar.sh
```

Na primeira vez o script cria o ambiente Python em `.venv` e instala as dependências (precisa de internet e leva alguns minutos). Nas seguintes, só sobe o painel e abre o navegador em http://localhost:8501. Para parar, `Ctrl+C`.

| Comando | O que faz |
|---|---|
| `./rodar.sh` | Painel na porta 8501 |
| `./rodar.sh --porta 8502` | Outra porta (use se a 8501 já estiver ocupada) |
| `./rodar.sh --runs pasta/` | Outra pasta de levantamentos |

O script usa o `uv` se ele estiver instalado; senão, procura um Python 3.10 a 3.13.

## Instalar à mão

Só é preciso para usar a linha de comando `paviscan` direto ou rodar os testes; o `rodar.sh` já faz isto sozinho.

```bash
python3.12 -m venv .venv && source .venv/bin/activate   # ou: uv venv --python 3.12 .venv
pip install -e ".[dev]"
```

Use Python 3.10 a 3.13 (as rodas do rasterio e do OpenCV ainda não cobrem versões mais novas). A instalação precisa ser editável (`-e`): é assim que `config/` é encontrado.

## Usar pelo painel

```bash
./rodar.sh                     # ou, com o ambiente ativado: paviscan painel --runs runs/
```

Na aba **Nova análise**:

1. **Imagem.** Envie o GeoTIFF (até 4 GB) ou aponte o caminho do arquivo no disco. A imagem é conferida e guardada no acervo (`dados/<nome>/orto.tif`) e pode ser reprocessada depois.
2. **Onde está a via.** Três caminhos: automático pelas ruas do OpenStreetMap; desenhar o eixo da via em cima da imagem; ou enviar um arquivo (polígono da pista ou linha do eixo). A superfície fica em "automática" por padrão: cada via é tratada como asfalto se pelo menos 30 % do seu corredor tem cor de asfalto, senão como terra. Dá para forçar asfalto ou terra.
3. **Processar.** Leva de 30 s a alguns minutos, conforme o tamanho e o formato da imagem.

O modo automático envia o retângulo geográfico da imagem ao servidor público do OpenStreetMap. Nos outros dois nada sai da máquina.

As demais abas:

| Aba | O que mostra |
|---|---|
| Visão geral | Condição geral (conceito A a E), defeitos, área, % afetada e custo de reparo; mapa com a ortofoto, condição por trecho e relevo 3D; trechos prioritários com foto |
| Vias e trechos | Conceito e índice por via, perfil ao longo da pista, piores segmentos |
| Inspeção | Revisão manual: marcar cada área como defeito ou falso positivo, corrigir o contorno e adicionar área que faltou |
| Orçamento | Plano recomendado e alternativas, por via e por serviço, com preços, BDI, revestimento e terreno ajustáveis |
| Dados e relatório | Relatório executivo em HTML (imprime em PDF), tabela e downloads CSV, XLSX e GeoJSON |
| Próximos passos | O caminho para o modelo treinado, com a base pública UAV-PDD2023 |

Os filtros da barra lateral (tipo de defeito, área, severidade, confiança, via, segmento) valem para todas as abas, inclusive orçamento e relatório.

### Revisão manual

Na aba **Inspeção** há três modos:

- **Triagem rápida.** Cada área aparece em recorte de resolução cheia, com ✓ (é defeito), ✗ (falso positivo) e ↺ (voltar a pendente). A fila mostra primeiro o que ainda não foi conferido.
- **Corrigir contorno.** A área abre num mapa com a ortofoto em resolução cheia; arraste os vértices ou desenhe um polígono novo. As medidas, a severidade e o segmento são recalculados.
- **Adicionar área que faltou.** Escolha o trecho e contorne o que o detector não achou.

Falso positivo sai dos indicadores, do orçamento e do relatório (há uma chave na barra lateral para incluí-los de volta). A revisão fica em `runs/<nome>/revisao.json` e nunca altera o que o detector gravou: dá para desfazer, e o par detecção + revisão serve de rótulo para treinar um modelo depois. Reprocessar a imagem renumera os defeitos e invalida a revisão daquele levantamento.

## Usar pela linha de comando

| Comando | O que faz |
|---|---|
| `paviscan run --orto X.tif --saida runs/nome` | Pipeline completo, com a pista pelo OpenStreetMap |
| `paviscan run --orto X.tif --pista P.geojson --saida runs/nome` | Com o polígono da pista desenhado por você |
| `paviscan run --orto X.tif --eixo E.geojson --superficie terra --saida runs/nome` | Com o eixo da via desenhado; `--superficie` aceita `auto` (padrão), `asfalto` ou `terra` |
| `paviscan pista --orto X.tif --saida P.geojson [--eixo E.geojson] [--superficie terra]` | Só gera a pista |
| `paviscan oam <link ou id> <pasta>` | Baixa uma ortofoto do OpenAerialMap |
| `paviscan sintetico <pasta> --seed 7` | Cena sintética com gabarito |
| `./scripts/verify.sh` | Lint + testes unitários + golden. É a definição de pronto |

Imagens de trabalho:

| Pasta | Local | GSD | Origem |
|---|---|---|---|
| `dados/querari` | Querari (AM), via de asfalto muito gasto | 2,7 cm | ortofoto própria, em graus e com banda alfa; eixo desenhado em `eixo.geojson` |
| `dados/Uiramut__6_PEF_Ortomosaic` | Uiramutã (RR), estrada de terra e ruas internas | 3,3 cm | ortofoto própria; pista automática, com superfície decidida por via |
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

**Via de terra.** Quando a maior parte da pista de uma janela tem cor forte de solo, a janela é tratada como via de terra: o filtro "asfalto é cinza" é trocado pelo de vegetação, e só se procura anomalia escura (buraco, poça, sulco com sombra). Não existe revestimento para perder numa via que já é solo.

A confiança junta contraste e nitidez da borda (e forma compacta, no buraco). Todos os limiares estão em `config/params.yaml`.

Na cena sintética (sementes 7, 11, 23, 42; GSD de 2, 3 e 5 cm): recall de 100 %, zero falso positivo, erro mediano de área de 3 a 6 %, zero detecção em pista limpa.

## Condição e orçamento

- **Índice de condição** (0 a 100) e **conceito** (A a E) por segmento e por via: perde 8 pontos por 1 % de área afetada e 1 ponto por buraco a cada 100 m (`condicao` no `params.yaml`). Revestimento perdido pesa só pela área: seus pedaços não entram na contagem por 100 m. É um índice de trabalho para ordenar trechos. Não é IGG nem ICP de norma.
- **Orçamento.** Três soluções em `config/solucoes.csv`: A, tapa-buraco superficial; B, remendo profundo; C, fresagem e recapeamento do trecho inteiro, quando a área de corte passa de 15 % do segmento. O plano recomendado usa A para buraco pequeno ou médio, B para buraco grande e para revestimento perdido, e troca por C nos trechos críticos. O BDI entra só no total.
- O plano recomendado pode sair mais caro que remendar tudo (opção B), porque recapear o trecho inteiro custa mais que remendar só as áreas. É efeito do gatilho de 15 % e dos preços; ajuste os dois na tela. A opção C orça sempre CBUQ, qualquer que seja o revestimento escolhido, porque é assim que está em `config/solucoes.csv`. A folga de corte vale para buracos; revestimento perdido é orçado pela própria área vezes 1,2.
- **Os preços de `config/precos.csv` são ilustrativos** (fonte `PLACEHOLDER`). O painel avisa enquanto houver um deles. Troque pelos de referência (SICRO, SINAPI ou contrato) antes de usar qualquer valor.

## Limites, para dizer na apresentação

- **Trecho que perdeu o revestimento na largura toda pode sair como bom.** O revestimento perdido é medido contra o pavimento inteiro que sobrou em volta, numa janela de 6 m. Se não sobrou pelo menos um quinto de pavimento inteiro nessa janela, não há com o que comparar: num teste com uma faixa de 6,4 m de largura toda sem capa, só as pontas foram medidas (17 % da área). Confira à mão, na aba Inspeção, os trechos de via muito gasta que saírem com conceito bom, e use "Adicionar área que faltou".
- **Imagem feita com sol baixo traz sombra longa, e sombra engana o método.** No asfalto, a sombra é retirada da busca por revestimento perdido, mas a borda dela e manchas claras de areia ainda aparecem, em geral com confiança abaixo de 0,35. Na estrada de terra, sombra de árvore aparece como buraco. Prefira voar perto do meio-dia; com imagem assim, use confiança mínima de 0,5 e a revisão manual.
- Os números são candidatos a defeito. Mancha de óleo, remendo escuro, sarjeta, sombra pequena e veículo escuro aparecem como buraco; lama, areia, calçada, concreto de cor quente, veículo de cor quente e folha seca aparecem como revestimento perdido. Nas imagens de Manaus a maior parte dos candidatos é isso. No Querari, onde o dano é extenso e visível, o contorno acompanha bem as áreas sem capa. O filtro de confiança e a aba Inspeção existem para essa triagem.
- O custo herda esses erros e usa preços ilustrativos: serve para mostrar o método, não para fechar contrato.
- O detector serve para buracos de até cerca de 2 m. A imagem é processada em janelas que se sobrepõem 4 m; a menos de 1 m da borda de uma janela o detector perde contexto, então a detecção ali fica para a janela vizinha. Buraco de até 2 m sai sempre inteiro de alguma janela. Acima disso ele pode sair truncado ou dividido: em geral fica a maior versão vista, marcada na coluna `na_emenda`, com aviso no processamento, no painel e em `cortadas_por_janela` do `resumo.json`. A marca não cobre tudo: objeto com mais de 2 m nas duas dimensões, objeto muito longo (6 m) e buraco grande que o detector divide em partes podem sair errados ou pela metade sem marca nenhuma.
- O resultado depende um pouco da grade de janelas, porque o limiar e o fundo são calculados por janela. Trocando o tamanho da janela nas imagens de Manaus, os totais mudaram de 1 a 5 % e cerca de 12 % dos candidatos fracos entraram ou saíram.
- A pista automática depende do OpenStreetMap estar alinhado com a ortofoto. Onde não há rua mapeada (caso do Querari), desenhe o eixo. A pista é segmentada pelo eixo de cada trecho reto. Entram ruas, estradas, vias de serviço (ruas internas de quartel e condomínio) e trilhas de veículo mapeadas.
- Em via de terra, a pista é tudo que não é vegetação nem telhado claro em volta do eixo: acostamento e pátio de terra encostados na via entram junto. Sombra de árvore na beira aparece como buraco.
- Eixo desenhado fora do centro da via perde parte da pista no modo asfalto (1,5 m de desvio tirou 17 % da pista num teste). No modo terra, o acostamento sem vegetação entra como pista (cerca de 10 % a mais no mesmo teste).
- Região de revestimento perdido que cai fora de todos os segmentos, ou pedaço menor que 0,05 m² depois de repartido, é descartada sem aviso.
- Severidade por área, em três faixas de trabalho. Não é classificação de norma.
- Imagem em graus (EPSG:4326) é reprojetada durante a leitura, o que deixa o processamento bem mais lento (cerca de 4 min no Querari, contra 30 s nas imagens já em UTM). GeoTIFF sem pirâmides e gravado em faixas, como o do Uiramutã (1,2 GB), também é lento: só a geração da pista leva 3 min. Regravar a imagem como COG (com blocos e pirâmides) resolve.
- Fora do escopo: comparação entre datas, profundidade, trincas.

## Créditos dos dados

Ortofotos de Manaus: Perito Paulo Rafael, CC-BY 4.0, via OpenAerialMap. Vias: © colaboradores do OpenStreetMap, ODbL. Base UAV-PDD2023: Yan e Zhang, 2023, Zenodo (https://zenodo.org/records/8429208).
