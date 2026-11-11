# PaviScan

Pipeline em Python: ortofoto de pista asfaltada -> buracos como polígonos em UTM -> medidas e severidade -> indicadores por segmento -> dashboard Streamlit com filtros. Retrato de uma imagem só: sem orçamento e sem série temporal. Plano completo em `PLAN.md`. Estado atual em `PROGRESS.md` (leia no início de toda sessão, atualize no fim).

## Comandos

```bash
pip install -e ".[dev]"
./scripts/verify.sh            # lint + testes + golden. É a definição de pronto
pytest tests/golden -q         # só os testes de aceite
python -m paviscan.sintetico dados/sint --seed 7    # gera cena de teste com gabarito
paviscan run --orto X.tif --pista pista.geojson --saida runs/nome   # sem --pista: pista pelo OpenStreetMap
paviscan pista --orto X.tif --saida pista.geojson   # só a pista automática
paviscan oam <link ou id do OpenAerialMap> dados/nome   # baixa a ortofoto
paviscan painel --runs runs/   # abre o dashboard
```

## Estrutura

```
src/paviscan/
  sintetico.py    PRONTO. Gera ortofoto + pista + gabarito. Não mudar a assinatura
  ingestao.py     M1  abrir raster em janelas, SRC, GSD, reprojeção para UTM
  pista.py        M1  máscara da pista; M3 segmentos ao longo do eixo
  deteccao/       M2  base.py (Protocol), classico.py, yolo.py (futuro)
  geometria.py    M2  máscara -> polígonos UTM com medidas e severidade
  indicadores.py  M3  agregação por segmento e resumo
  exportar.py     M3  GeoJSON, GPKG, XLSX, previa.png
  painel/         M4  dados.py (funções puras) e app.py (Streamlit)
  pipeline.py     M1+ orquestra tudo
  cli.py          M1+ typer
  config.py       leitura do params.yaml
  pista_osm.py    extra: pista automática (eixos do OpenStreetMap + cor do asfalto)
  oam.py          extra: baixa ortofoto do OpenAerialMap
config/params.yaml
tests/golden/     testes de aceite. BLOQUEADOS para edição
tests/unit/       testes que você escreve
```

## Contratos (os testes golden dependem deles)

```python
# paviscan/pipeline.py
def processar(orto: Path, pista: Path, saida: Path, config: Path | None = None) -> Path
# grava em saida/ e devolve saida:
#   defeitos.geojson   SRC UTM. Colunas: id, area_m2, largura_m, comprimento_m,
#                      confianca (0 a 1), severidade ("baixa"|"media"|"alta"), segmento (int)
#   segmentos.geojson  SRC UTM, cobre a pista inteira. Colunas: segmento, area_pista_m2,
#                      n_buracos, area_buracos_m2, pct_afetada (0 a 100)
#   resumo.json        gsd_m, n_defeitos, area_total_m2, area_pista_m2, pct_afetada,
#                      detector, versao, previa_limites ([[lat_s, lon_o], [lat_n, lon_l]])
#   previa.png         ortofoto reduzida (no máximo ~4000 px de largura) para o dashboard

# paviscan/painel/dados.py  (sem importar streamlit)
def carregar(run: Path) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, dict]   # defeitos, segmentos, resumo
def filtrar(defeitos, area_min=None, area_max=None, confianca_min=None,
            severidade=None, segmentos=None) -> gpd.GeoDataFrame    # None = não filtra
def resumir(defeitos, segmentos) -> dict
# chaves: n_buracos, area_total_m2, pct_afetada, buracos_por_100m, maior_buraco_m2
# pct_afetada usa a área de pista dos segmentos; funciona com defeitos já filtrados

# paviscan/deteccao/base.py
class Detector(Protocol):
    nome: str
    versao: str
    def detectar(self, img: np.ndarray, gsd_m: float, valida: np.ndarray) -> list[Deteccao]: ...
    # img: RGB uint8 HxWx3. valida: bool HxW (True = pista). Sem estado entre chamadas.

@dataclass
class Deteccao:
    mascara: np.ndarray   # bool HxW, um buraco por detecção
    confianca: float      # 0 a 1
    classe: str = "panela"
```

- `largura_m` e `comprimento_m` são os lados do retângulo mínimo rotacionado (largura <= comprimento).
- `severidade` vem dos limites de área em `config/params.yaml`.
- `segmento` é o índice do trecho de `segmento_m` metros ao longo do eixo maior da pista em que cai o centroide do buraco.
- A pista pode ter várias feições (uma por via). Cada uma é segmentada no próprio eixo; `segmento` é sequencial e `via` guarda o nome. `segmentos.geojson` também tem `via` e `comprimento_m`, e `defeitos.geojson` tem `via`.
- `processar` aceita um quinto argumento opcional, `progresso(fracao, texto)`, usado pela CLI e pelo painel.
- O detector é escolhido por `detector:` em `params.yaml`. Nada fora de `deteccao/` pode saber qual detector está rodando.

## Regras

- Toda medida é feita em SRC projetado em metros. Entrada em graus é reprojetada para a zona UTM do centroide antes de qualquer cálculo.
- Nunca carregar o raster inteiro: ler por janelas (`rasterio.windows`), com sobreposição para o buraco não ser cortado na borda.
- Limiares moram em `config/`, em metros, nunca em pixel e nunca no código.
- Não editar `tests/golden/` nem `src/paviscan/sintetico.py` para fazer teste passar. Se o teste parecer errado, pare e explique ao usuário.
- Fora do escopo: orçamento, comparação entre datas, profundidade, trincas. Não implemente sem o usuário pedir.
- Código e nomes em português sem acento, docstrings curtas, type hints, sem classes onde função resolve.
- Um commit por tarefa, mensagem em português no imperativo. Não dar push sem o usuário pedir.

## Como trabalhar

1. Leia `PROGRESS.md` e a linha do marco em `PLAN.md`.
2. Planeje em tarefas pequenas. Tarefas independentes vão para especialistas em paralelo; dependentes, em sequência.
3. Ao delegar, passe ao subagente: arquivos a criar, o contrato acima que se aplica, e o teste que precisa passar. Ele não vê esta conversa.
4. Terminou de codar: `./scripts/verify.sh`. Depois chame o `verificador` (ele não viu o código ser escrito). Só é "pronto" com o veredito APROVADO.
5. Atualize `PROGRESS.md`, faça commit.

Especialistas em `.claude/agents/`: `geo-raster`, `cv-detector`, `dashboard`, `verificador`.
