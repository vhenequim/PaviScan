# Progresso

Atualize ao fim de cada sessão. Curto e factual.

## Estado

| Marco | Status | Observação |
|---|---|---|
| M1 Ingestão e pista | feito (11/11) | Leitura por janelas, reprojeção para UTM, CLI |
| M2 Detecção e geometria | feito (11/11) | Golden de detecção passa; 8/8 de recall, 0 FP, erro de área de 3 % |
| M3 Indicadores, exportações e imagem real | feito (11/11) | Golden de indicadores passa; duas ortofotos reais processadas |
| M4 Dashboard | feito (11/11) | Golden do painel passa; conferido no navegador |
| M5 Fechamento | em parte | README feito e produto ampliado (ver rodada de produto). Faltam roteiro da apresentação, ensaio e vídeo de reserva |

O MVP foi feito de uma vez em 11/11, a pedido, sem a divisão por marcos.

## Feito
- Scaffold, harness, gerador sintético e testes golden.
- Pipeline completo: `ingestao`, `pista`, `deteccao/` (interface + clássico), `geometria`, `indicadores`, `exportar`, `pipeline`, `cli`.
- Dashboard (`painel/dados.py` e `painel/app.py`): filtros de área, severidade, confiança, via e segmento; indicadores; mapa com ortofoto; gráficos; ranking; conferência visual com recortes; tabela com CSV, XLSX e GeoJSON; bloco para processar imagem nova.
- Extras que o plano não previa, necessários para os insumos recebidos:
  - `pista_osm.py` / `paviscan pista`: pista automática pelos eixos do OpenStreetMap + cor do asfalto. As duas imagens são bairros inteiros, sem polígono de pista.
  - `oam.py` / `paviscan oam`: baixa a ortofoto do OpenAerialMap pelo link.
  - `recortes/<id>.jpg`: miniatura de cada buraco para conferir no painel.
- Testes unitários em `tests/unit/` (41) com números conhecidos, incluindo RGBA, entrada em graus, uint16 e emenda de janelas.

## Resultado nas imagens reais (11/11, detector 0.3.0)

| Imagem | GSD | Pista | Candidatos | Área | % afetada | Confiança >= 0,8 | Tempo |
|---|---|---|---|---|---|---|---|
| Novo Aleixo | 1,8 cm | 8.312 m², 93 segmentos | 318 | 51,9 m² | 0,62 % | 23 | ~28 s |
| Tarumã | 3,0 cm | 11.976 m², 155 segmentos | 404 | 63,1 m² | 0,53 % | 56 | ~20 s |

Uma detecção em cada imagem saiu marcada como `na_emenda` (possivelmente truncada).

O que o detector errou (olhando os recortes):
- Sarjeta e pé de meio-fio escuros, trinca larga, mancha úmida e remendo de asfalto novo viram candidato. No Tarumã o pavimento é muito remendado, e remendo escuro é indistinguível de buraco para este método.
- Moto, pessoa, cachorro e sombra de veículo aparecem quando são escuros e pouco coloridos.
- Os candidatos de confiança >= 0,85 no Tarumã são, na maioria, buraco ou remendo real. No Novo Aleixo a mistura é pior (mais borda de calçada).
- Estes números são contagem de candidatos, não de buracos confirmados. Não há gabarito real para medir precisão.

## Revisão independente (11/11)

Primeira passada do verificador: REPROVADO, com `verify.sh` OK. Corrigido em seguida, com teste:
- Ortofoto com banda alfa (RGBA) quebrava na prévia. Agora alfa, nodata, 1 banda e uint16 são tratados; pista sobre área sem dado sai do denominador.
- Painel: com filtro de via ou segmento, "% afetada" e "buracos por 100 m" passam a usar só os segmentos do recorte.
- Janelas: a sobreposição mínima de 4 m não cede mais (a janela cresce); detecção grande cortada na emenda gera aviso e fica registrada no resumo.
- Mesmo buraco em janelas vizinhas: fica a versão mais central, sem união de polígonos. Os filtros de forma voltam a valer na saída.
- Separação de buracos colados reescrita (núcleos pela transformada de distância); resultado estável com a posição.
- Trechos de pista sobrepostos são aparados com aviso; pista que não cruza a ortofoto é erro; `--config` inexistente é erro.
- Limiares que estavam no código foram para `config/params.yaml`; sobra de trecho menor que 5 m entra no último segmento.

Segunda passada: REPROVADO de novo, com `verify.sh` OK. Os oito experimentos da primeira passada foram dados como corrigidos, mas a troca de `unir` por `deduplicar` tinha um erro: detecções vizinhas que só se encostam eram apagadas por arredondamento (cerca de 4 % dos candidatos nas imagens reais). Corrigido:
- `deduplicar` só trata como o mesmo buraco quando a interseção cobre pelo menos 20 % do menor polígono (`tiles.duplicata_min`), e fica com a versão maior, não com a mais central. Teste em coordenadas reais não inteiras.
- Emenda de janelas: detecção a menos de 1 m de borda interna (`tiles.margem_emenda_m`) é descartada se couber na janela vizinha (até 2 m) e, se for maior, fica marcada em `na_emenda` com aviso.
- Limites que continuam: objeto com mais de ~2 m nas duas dimensões é medido de forma instável (a janela de fundo local tem 2 m) e pode sair errado sem aviso. Está escrito no README.

Terceira passada: APROVADO, sem problema grave, `verify.sh` OK (41 unitários, 13 golden). O que o verificador deixou registrado como defeito não grave ou limite, e que está no README:
- Defeito conexo de 3 m ou mais que o detector divide em partes pode perder uma metade sem marca (2 de 92 posições no teste dele).
- A margem de 1 m na emenda deixa de fora 1 a 2 % de candidatos reais (5 de 404 no Tarumã, 7 de 318 no Novo Aleixo).
- "Maior área vence" junta duas detecções distintas quando outra janela as vê fundidas (1 de 404 e 5 de 318).
- O resultado depende da grade de janelas: totais variam 1 a 5 %, a lista de candidatos fracos ~12 %, a de confiança >= 0,8 quase nada. Vem do limiar e do fundo por janela e existe desde a primeira versão.
- Objeto com mais de ~2 m nas duas dimensões é medido de forma instável, sem marca.
- Raster que não é uint8 com mais de 0,5 % da área saturada derruba a escala de brilho sem aviso.

## Rodada de produto (11/11, tarde): entrada de arquivos, orçamento, condição, Querari

Pedido do usuário: aproximar do produto final, com a entrada de arquivos e a parte "meio que de orçamento" do repositório Petralanda/paviscan, testar no Querari e usar a base UAV-PDD2023 se ajudasse. Prioridade declarada: o visual para a apresentação vale mais que o algoritmo.

O que entrou:
- **Entrada de arquivos** (`acervo.py`, aba Nova análise): upload até 4 GB ou caminho no disco, gravação em arquivo parcial com conferência da georreferência, acervo em `dados/<nome>/`. A via pode vir do OpenStreetMap, de um eixo desenhado em cima da imagem (folium) ou de arquivo (polígono ou linha).
- **Pista por eixo desenhado** e superfície `terra` (tudo que não é vegetação nem telhado claro em volta do eixo): `paviscan run --eixo ... --superficie terra`.
- **Segunda classe de defeito**, `solo_exposto` (revestimento perdido): região extensa, medida como área, unida entre janelas e repartida por segmento. Campo novo `Deteccao.extensa`. Detector na versão 0.5.1.
- **Orçamento** (`orcamento.py`, `config/solucoes.csv`, `config/precos.csv`): opções A, B e C, plano recomendado, BDI só no total, `orcamento.csv` e `orcamento.xlsx`. Veio da especificação que estava no histórico deste repositório (commit 750f4bd). Preços continuam PLACEHOLDER.
- **Índice de condição** (0 a 100) e conceito A a E por segmento e por via (`condicao` no params.yaml). De trabalho, não é de norma.
- **Painel redesenhado** em abas: Visão geral (com relevo 3D e trechos prioritários), Vias e trechos, Inspeção, Orçamento, Dados e relatório, Nova análise, Próximos passos.
- **Relatório executivo** em HTML (`relatorio.py`), com os filtros atuais. Exemplos em `runs/querari/relatorio.html` e `runs/taruma/relatorio.html`.
- **Base UAV-PDD2023** (`base_publica.py`): só ilustração na aba Próximos passos. O zip veio truncado, mas as 2.440 anotações e 2.080 imagens são legíveis; extraí as anotações e 14 fotos para `dados/uavpdd`.

Achados:
- O repositório do Petralanda **não tem cálculo de orçamento**: no HEAD há só quantitativos (ranking, tabela, downloads). A especificação de custo existiu no histórico e foi removida.
- UAV-PDD2023 é quase só trinca (195 panelas em 11.158 anotações) e são fotos próximas, sem georreferência. Não serve para treinar detector de buraco sozinha.
- No Querari o dano é revestimento perdido em faixas longas, mais claro que o asfalto velho e de cor de solo. O detector de "anomalia escura" achava 2 m²; a segunda passada acha 167 m² (10,9 % da pista), com contorno coerente nos recortes.

Resultado (detector 0.5.2, preços ilustrativos, antes de qualquer revisão manual):

| Imagem | Pista | Defeitos | Área | % afetada | Buraco / Revest. perdido | Condição | Plano (ilustrativo) |
|---|---|---|---|---|---|---|---|
| Querari | 1.534 m² | 260 | 167,3 m² | 10,91 % | 2 / 258 | E · 12 | R$ 76,6 mil |
| Tarumã | 11.976 m² | 635 | 157,5 m² | 1,32 % | 404 / 231 | B · 76 | R$ 162,8 mil |
| Novo Aleixo | 8.312 m² | 539 | 109,7 m² | 1,32 % | 318 / 221 | B · 72 | R$ 98,2 mil |

A condição acima já usa o índice corrigido (pedaço de revestimento perdido pesa só pela área). Com a regra anterior, Tarumã e Novo Aleixo davam C (68 e 60).

No Querari, 7 dos 28 segmentos saem com conceito A ou B. Olhei quatro deles: três têm mesmo a superfície uniforme; o segmento 2 parece todo de terra e sai com A, que é o limite conhecido (trecho sem capa na largura toda não tem com o que comparar).

O que não foi feito ou conferido nesta rodada:
- A revisão do verificador veio depois (ver "Parecer da rodada de produto", abaixo).
- No navegador conferi as abas, o mapa 3D, o Querari e o mapa de desenho abrindo. **Não** exercitei de ponta a ponta, pela tela, o envio de arquivo, o traçado do eixo seguido de processamento, nem o botão do relatório; essas partes têm teste unitário ou foram rodadas pela linha de comando.
- Nas imagens de Manaus a classe `solo_exposto` tem muito falso positivo (asfalto mais claro, calçada, concreto), com confiança baixa. O filtro "Tipo de defeito" permite desligar.
- O Querari leva cerca de 4 min (reprojeção de EPSG:4326 durante a leitura).
- Nada foi commitado.

## Parecer da rodada de produto e revisão manual (11/11, fim do dia)

O verificador reprovou a rodada de produto (uma passada, `verify.sh` OK). Graves, todos tratados:
- Processar uma imagem com 2 ou mais levantamentos terminava em exceção na tela. Corrigido; há teste do painel para isso.
- O teto de 80 m² do revestimento perdido valia por pedaço de janela e apagava o dano maior. Teto removido; há teste.
- Trecho que perdeu a capa na largura toda não é detectado (não há pavimento inteiro para comparar). **Não corrigido**: está escrito no README e no texto de limites do detector.
- Eu tinha anotado conceito B para Tarumã e Novo Aleixo; o código dava C. Corrigido na tabela abaixo.

Menores tratados: relatório antigo oferecido depois de mudar o filtro; preço apagado virava NaN em silêncio; pedaço de região extensa contava como defeito na densidade do índice; área comum entre buraco e solo contava duas vezes; `stac_item.json` herdado ao substituir imagem; pasta vazia após envio inválido; `--eixo` ignorado quando já havia pista; superfície travada no modo "enviar arquivo"; tabelas de preço em cache sem validade; crédito imprimindo "None"; erros crus no eixo; constantes movidas para o `params.yaml`.

Menores que ficaram: o painel ainda soma por conta própria em um ou outro ponto de exibição; a opção C orça sempre CBUQ (regra da tabela); não há teste do upload nem do desenho no mapa.

**Revisão manual** (pedido do usuário): aba Inspeção com triagem (✓ defeito, ✗ falso positivo, ↺ pendente), correção de contorno e adição de áreas num mapa com a ortofoto em resolução cheia. `revisao.py` guarda tudo em `runs/<nome>/revisao.json`, sem alterar o que o detector gravou. Falso positivo sai dos indicadores, do orçamento e do relatório. As abas viraram navegação com chave: só a seção aberta é desenhada, e ela sobrevive a cada clique.

Não conferido: o desenho e a edição de polígono no mapa só foram exercitados à mão em parte (ver a mensagem final da sessão); os testes automáticos cobrem o módulo e os botões de triagem.

## Erro ao processar pela tela e via de terra (11/11, noite)

- O usuário processou uma imagem nova (Uiramutã, 6º PEF) e a tela terminou em `StreamlitWidgetAlreadyInstantiatedError`: ao corrigir o seletor de levantamento eu repeti o defeito na chave da navegação (`aba`). Corrigido com chave pendente; agora há um teste que clica em Processar de verdade e que falha no código anterior.
- O levantamento do Uiramutã tinha saído com 0 defeitos e 74 m² de pista. Causas: a estrada principal é de terra vermelha (o modo asfalto procura cinza) e as ruas internas do pelotão são `service` no OpenStreetMap, classe que ficava de fora.
- Mudanças: superfície `auto` por padrão, decidida via por via (`pista_osm.cobertura_asfalto_min`); classes `service` e `track` entram na pista automática; o detector trata como via de terra a janela cuja pista tem cor forte de solo (`classico.terra_croma_min`, `terra_fracao_min`), e ali procura só anomalia escura. Detector 0.6.0.
- A imagem do Uiramutã foi feita com sol baixo. No asfalto das ruas internas, a parte ao sol ao lado de uma sombra longa saía como "revestimento perdido". O detector agora tira a sombra (escura e sem cor quente) dessa passada (`classico.sombra_*`). Detector 0.6.1. Reduziu, não zerou: sobram manchas claras de areia e poeira sobre o asfalto e bordas de sombra, quase todas com confiança abaixo de 0,35.
- No que acerta: os sulcos de erosão na beira da estrada de terra saem bem contornados. No que erra: sombra de árvore sobre a terra vira buraco.

Resultado final de todos os levantamentos (detector 0.6.1, preços ilustrativos, sem revisão manual):

| Imagem | Pista | Defeitos | Área | % afetada | Buraco / Revest. perdido | Condição |
|---|---|---|---|---|---|---|
| Uiramutã | 13.357 m² | 917 | 388,9 m² | 2,91 % | 648 / 269 | D · 40 |
| Uiramutã, confiança >= 0,5 | 13.357 m² | 517 | 169 m² | 1,27 % | 477 / 40 | C · 63 |
| Querari | 1.534 m² | 260 | 165,5 m² | 10,79 % | 2 / 258 | E · 13 |
| Tarumã | 11.976 m² | 618 | 152,4 m² | 1,27 % | 404 / 214 | B · 76 |
| Novo Aleixo | 8.312 m² | 526 | 107,3 m² | 1,29 % | 318 / 208 | B · 72 |

O Uiramutã processa em cerca de 2 min; a geração da pista automática leva mais 3 min (arquivo de 1,2 GB sem pirâmides).

## Comando único e trabalho em aberto (12/11)

- `./rodar.sh` na raiz: cria o `.venv` e instala as dependências na primeira vez, depois sobe o painel. Documentado no topo do README.
- Em aberto, sem commit: o usuário reclamou do mapa borrado e do editor de contorno travado. Causas medidas: o mapa mostra uma imagem só de até 4000 px (25 cm/px no Uiramutã contra 3,3 cm nativos) e o contorno do detector chega a mais de mil vértices arrastáveis. Já aplicado na árvore de trabalho, mas sem rodar os testes de novo: orçamento vetorizado, contorno simplificado para editar (`revisao.para_edicao`), recorte do editor em JPEG. `painel/ladrilhos.py` (ortofoto em ladrilhos sob demanda) está escrito e ainda não ligado ao `_mapa`. O usuário mandou parar antes de terminar.

## Identidade visual (13/11)

- A pedido do usuário: símbolo do Exército à esquerda e distintivo do 2º Gpt E à direita na faixa de título (e no relatório), paleta verde-oliva, tema escuro e onça como indicador de carregamento. Tudo em `src/paviscan/marca/`.
- O EPS do Exército não foi convertido: a máquina não tem interpretador PostScript (Ghostscript). Usei o PNG de 1162 x 2000 px que veio na mesma pasta, reduzido a 360 px de altura.
- O distintivo do 2º Gpt E foi redesenhado em SVG a partir de um JPEG de 447 px: escudo, faixas e anel por medida na imagem, letras em Rockwell Bold convertidas em contorno, castelo desenhado por geometria. Não é a arte oficial.
- Temas: `[theme.light]` e `[theme.dark]` em `.streamlit/config.toml`; a troca é no menu ⋮. As cores dos elementos próprios usam `light-dark()` do CSS, porque o Streamlit põe `color-scheme` no `stApp` e não roda o script de novo ao trocar o tema.
- Carregamento: pseudo-elementos do `stApp` ligados a `data-test-script-state="running"`, com atraso de 0,5 s para clique rápido não piscar. Depende de nomes internos do Streamlit 1.67; se uma versão nova mudar os nomes, o indicador some sem quebrar nada.
- A onça veio de uma prévia do Dreamstime com marca d'água. O arquivo `marca/onca.svg` está no `.gitignore` até o usuário confirmar a licença ou trocar a imagem.

## Decisões
- 10/11: escopo reduzido a um retrato de uma imagem. Orçamento e série temporal saíram.
- 10/11: detector por anomalia escura local com limiar robusto (mediana + k x MAD), não Otsu nem watershed puro. YOLO do mestrado entra depois pela interface `Detector`.
- 10/11: saída visual é um dashboard Streamlit com filtros de área, severidade, confiança e segmento.
- 11/11: fundo local em duas passadas (a segunda ignora os pixels já anômalos), para buraco grande não puxar a mediana.
- 11/11: o detector ignora pixel colorido (`croma_max`) e a confiança ganhou um termo de forma (`solidez_cheia`). Detector na versão 0.3.0.
- 11/11: pista com várias feições é segmentada feição a feição; `segmento` é sequencial e `via` guarda o nome. `segmentos.geojson` ganhou `via` e `comprimento_m`.
- 11/11: `previa.png` é gravada já alinhada a EPSG:4326, para o mapa não ficar torto (a convergência de meridianos dá ~1 m em 400 m).
- 11/11: janelas vizinhas se sobrepõem pelo menos 4 m; detecção cortada por borda interna de janela é descartada e vem inteira da vizinha. Do mesmo buraco em duas janelas fica a versão maior.
- 11/11: a pista automática consulta só o servidor oficial do Overpass (envia o retângulo da imagem). Com `--pista`, nada sai da máquina.
- 11/11: `pyproject.toml` ignora a regra de ordem de import (I001) em `tests/golden/`. O arquivo não foi tocado; a ordem dele passou a ser "errada" para o ruff quando `pipeline.py` passou a existir.
- 11/11: `streamlit>=1.50` (o painel usa `width="stretch"`).

- 11/11: o usuário aprovou o que entrou além do plano: pista automática pelo OpenStreetMap (`pista_osm.py`), download do OpenAerialMap (`oam.py`), as linhas acrescentadas ao `CLAUDE.md` (comandos, estrutura, `via`, `comprimento_m`, `progresso`) e a exceção de lint I001 em `tests/golden/`.

## Pendências e dúvidas para o usuário
- Preços reais para `config/precos.csv` (SICRO, SINAPI ou contrato). Sem isso o valor em reais é só ilustração.
- Decidir se a classe `solo_exposto` fica ligada por padrão em via urbana de asfalto (`classico.solo_exposto`).
- Conferir no `CLAUDE.md` as linhas que acrescentei nesta rodada (orçamento de volta ao escopo, módulos novos, campos novos).
- O `CLAUDE.md` ainda não registra: pista recortada pela área com dado (o contrato diz "cobre a pista inteira"), erro quando a pista não cruza a ortofoto, coluna `na_emenda` e chaves `cortadas_por_janela` e `limites_detector` do resumo. Não editei o arquivo por conta própria nesta parte; proponho acrescentar.
- `area_max_m2` está em 10 m², mas o detector só é confiável até ~2 m de lado. Decidir: baixar o limite (4 m² tira carro escuro e objeto grande instável) ou aumentar `janela_fundo_m`.
- Confirmar as faixas de severidade em `config/params.yaml` (0,25 e 1,0 m²).
- Escolher o trecho da demo. Um polígono de pista desenhado à mão num trecho ruim do Tarumã deve dar um resultado mais limpo que o bairro inteiro.
- Pesos do YOLO do mestrado, quando houver: entram em `deteccao/yolo.py` sem mexer no resto.
- Roteiro, ensaio e vídeo de reserva (M5).
