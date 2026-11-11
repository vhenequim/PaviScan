# Progresso

Atualize ao fim de cada sessão. Curto e factual.

## Estado

| Marco | Status | Observação |
|---|---|---|
| M1 Ingestão e pista | feito (11/11) | Leitura por janelas, reprojeção para UTM, CLI |
| M2 Detecção e geometria | feito (11/11) | Golden de detecção passa; 8/8 de recall, 0 FP, erro de área de 3 % |
| M3 Indicadores, exportações e imagem real | feito (11/11) | Golden de indicadores passa; duas ortofotos reais processadas |
| M4 Dashboard | feito (11/11) | Golden do painel passa; conferido no navegador |
| M5 Fechamento | em parte | README feito. Faltam roteiro da apresentação, ensaio e vídeo de reserva |

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
- O `CLAUDE.md` ainda não registra: pista recortada pela área com dado (o contrato diz "cobre a pista inteira"), erro quando a pista não cruza a ortofoto, coluna `na_emenda` e chaves `cortadas_por_janela` e `limites_detector` do resumo. Não editei o arquivo por conta própria nesta parte; proponho acrescentar.
- `area_max_m2` está em 10 m², mas o detector só é confiável até ~2 m de lado. Decidir: baixar o limite (4 m² tira carro escuro e objeto grande instável) ou aumentar `janela_fundo_m`.
- Confirmar as faixas de severidade em `config/params.yaml` (0,25 e 1,0 m²).
- Escolher o trecho da demo. Um polígono de pista desenhado à mão num trecho ruim do Tarumã deve dar um resultado mais limpo que o bairro inteiro.
- Pesos do YOLO do mestrado, quando houver: entram em `deteccao/yolo.py` sem mexer no resto.
- Roteiro, ensaio e vídeo de reserva (M5).
