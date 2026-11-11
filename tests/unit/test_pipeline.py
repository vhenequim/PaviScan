"""Pipeline em entradas que o golden nao cobre: alfa, graus, uint16, pista fora, config."""

import json
import warnings

import cv2
import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.enums import ColorInterp
from rasterio.warp import Resampling, calculate_default_transform, reproject
from rasterio.windows import Window
from shapely import affinity

from paviscan import pipeline, sintetico
from paviscan.deteccao import Deteccao


@pytest.fixture(scope="module")
def cena(tmp_path_factory):
    pasta = tmp_path_factory.mktemp("cena_curta")
    return sintetico.gerar_cena(pasta, n_buracos=4, gsd=0.03, comprimento_m=24.0, seed=11)


def _n_casados(saida, cena) -> tuple[int, int]:
    det = gpd.read_file(saida / "defeitos.geojson")
    gab = gpd.read_file(cena / "gabarito.geojson").to_crs(det.crs)
    achados = sum(any(d.intersects(g) for d in det.geometry) for g in gab.geometry)
    return achados, len(det)


def test_ortofoto_com_banda_alfa(cena, tmp_path):
    """RGBA e a exportacao padrao de WebODM, Pix4D e Metashape."""
    with rasterio.open(cena / "orto.tif") as src:
        dados, perfil = src.read(), src.profile
    perfil.update(count=4)
    alfa = np.full(dados.shape[1:], 255, np.uint8)
    alfa[:, :100] = 0  # os primeiros 3 m da imagem nao tem dado
    with rasterio.open(tmp_path / "rgba.tif", "w", **perfil) as dst:
        dst.write(dados, [1, 2, 3])
        dst.write(alfa, 4)
        dst.colorinterp = [ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha]
    saida = pipeline.processar(tmp_path / "rgba.tif", cena / "pista.geojson", tmp_path / "run")
    for nome in ("defeitos.geojson", "segmentos.geojson", "resumo.json", "previa.png"):
        assert (saida / nome).exists(), nome
    resumo = json.loads((saida / "resumo.json").read_text())
    # a pista sob "sem dado" sai do denominador: 24 m x 7 m menos a faixa de 3 m sem dado
    assert abs(resumo["area_pista_m2"] - (24 - 3) * 7) < 2.0
    previa = cv2.imread(str(saida / "previa.png"), cv2.IMREAD_UNCHANGED)
    assert previa.shape[2] == 4 and (previa[..., 3] == 0).any() and (previa[..., 3] == 255).any()


def test_ortofoto_em_graus_e_reprojetada_para_utm(cena, tmp_path):
    with rasterio.open(cena / "orto.tif") as src:
        tf, w, h = calculate_default_transform(
            src.crs, "EPSG:4326", src.width, src.height, *src.bounds
        )
        perfil = src.profile | {"crs": "EPSG:4326", "transform": tf, "width": w, "height": h}
        with rasterio.open(tmp_path / "graus.tif", "w", **perfil) as dst:
            for b in (1, 2, 3):
                reproject(
                    rasterio.band(src, b), rasterio.band(dst, b), resampling=Resampling.bilinear
                )
    saida = pipeline.processar(tmp_path / "graus.tif", cena / "pista.geojson", tmp_path / "run")
    det = gpd.read_file(saida / "defeitos.geojson")
    assert det.crs.is_projected and det.crs.to_epsg() == 31980  # zona UTM do centroide (20S)
    achados, total = _n_casados(saida, cena)
    assert achados == 4 and total <= 5
    resumo = json.loads((saida / "resumo.json").read_text())
    assert abs(resumo["gsd_m"] - 0.03) < 0.002


def test_ortofoto_uint16_usa_uma_escala_so(cena, tmp_path):
    with rasterio.open(cena / "orto.tif") as src:
        dados, perfil = src.read().astype(np.uint16) * 200, src.profile
    perfil.update(dtype="uint16")
    with rasterio.open(tmp_path / "u16.tif", "w", **perfil) as dst:
        dst.write(dados)
    saida = pipeline.processar(tmp_path / "u16.tif", cena / "pista.geojson", tmp_path / "run")
    achados, total = _n_casados(saida, cena)
    assert achados == 4 and total <= 5
    previa = cv2.imread(str(saida / "previa.png"), cv2.IMREAD_UNCHANGED)
    assert previa.dtype == np.uint8
    assert 60 < previa[..., 0][previa[..., 3] > 0].mean() < 200  # nem preta nem estourada


def test_pista_que_nao_cruza_a_ortofoto_e_erro(cena, tmp_path):
    pista = gpd.read_file(cena / "pista.geojson")
    pista["geometry"] = pista.geometry.apply(lambda g: affinity.translate(g, 5000, 0))
    pista.to_file(tmp_path / "longe.geojson", driver="GeoJSON")
    with pytest.raises(ValueError, match="nao cruza"):
        pipeline.processar(cena / "orto.tif", tmp_path / "longe.geojson", tmp_path / "run")
    assert not (tmp_path / "run" / "resumo.json").exists()


def test_config_pedido_e_inexistente_e_erro(cena, tmp_path):
    with pytest.raises(FileNotFoundError):
        pipeline.processar(
            cena / "orto.tif",
            cena / "pista.geojson",
            tmp_path / "run",
            tmp_path / "nao_existe.yaml",
        )


def _det(forma, l0, l1, c0, c1) -> Deteccao:
    m = np.zeros(forma, dtype=bool)
    m[l0 : l1 + 1, c0 : c1 + 1] = True
    return Deteccao(m, 0.9)


def test_perto_da_borda_interna_a_pequena_sai_e_a_grande_fica_marcada():
    janela = Window(100, 0, 200, 200)  # encosta no topo do raster; as outras bordas sao internas
    dets = [
        _det((200, 200), 80, 90, 80, 90),  # no meio: fica
        _det((200, 200), 0, 10, 80, 90),  # toca o topo, que e borda do raster: fica
        _det((200, 200), 100, 110, 0, 10),  # toca a esquerda (interna) e e pequena: sai
        _det((200, 200), 100, 110, 185, 192),  # a 7 px da direita, dentro da margem: sai
        _det((200, 200), 120, 199, 60, 70),  # toca a base e nao cabe na sobreposicao util: fica
    ]
    boas, folgas, emendas = pipeline._inteiras(
        dets, janela, largura=1000, altura=1000, sobre=60, margem=10
    )
    assert len(boas) == 3
    assert emendas == [False, False, True]
    assert folgas == [80, 80, 0]


def test_dois_buracos_colados_em_georreferencia_real_saem_os_dois(tmp_path):
    """Regressao: as duas partes do watershed se encostam e uma sumia por arredondamento."""
    from rasterio.transform import Affine
    from shapely.geometry import box

    rng = np.random.default_rng(4)
    cinza = (100 + 4 * rng.standard_normal((400, 520))).astype(float)
    yy, xx = np.mgrid[:400, :520]
    for cx in (150, 370):
        cinza[(yy - 200) ** 2 + (xx - cx) ** 2 <= 25**2] -= 50
    ponte = (abs(yy - 200) <= 6) & (xx > 150) & (xx < 370)
    cinza[ponte] = np.minimum(cinza[ponte], 55)
    img = np.repeat(cinza.clip(0, 255).astype(np.uint8)[None], 3, axis=0)
    for k, (gsd, e0, n0) in enumerate(
        [(0.029935668143705744, 828405.768963852, 9667742.037776284),
         (0.017817899999997847, 836540.444720372, 9661836.08844723),
         (0.02, 830000.0, 9660000.0)]
    ):  # fmt: skip
        tf = Affine(gsd, 0, e0, 0, -gsd, n0)
        orto = tmp_path / f"orto{k}.tif"
        perfil = dict(driver="GTiff", count=3, dtype="uint8", height=400, width=520,
                      crs="EPSG:31980", transform=tf)  # fmt: skip
        with rasterio.open(orto, "w", **perfil) as dst:
            dst.write(img)
        pista = gpd.GeoDataFrame(
            {"nome": ["pista"]}, geometry=[box(e0, n0 - 400 * gsd, e0 + 520 * gsd, n0)],
            crs="EPSG:31980",
        )  # fmt: skip
        pista.to_file(tmp_path / f"pista{k}.geojson", driver="GeoJSON")
        saida = pipeline.processar(orto, tmp_path / f"pista{k}.geojson", tmp_path / f"run{k}")
        assert len(gpd.read_file(saida / "defeitos.geojson")) == 2, gsd


def test_janela_cresce_para_manter_a_sobreposicao_minima(tmp_path):
    """Com pixel de 4 mm, 4 m de sobreposicao nao cabem em meia janela de 1024 px."""
    cena = sintetico.gerar_cena(tmp_path / "fina", n_buracos=3, gsd=0.004, comprimento_m=10, seed=5)
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # nenhuma deteccao pode ser cortada na emenda
        saida = pipeline.processar(cena / "orto.tif", cena / "pista.geojson", tmp_path / "run")
    achados, total = _n_casados(saida, cena)
    assert achados == 3 and total <= 4
    assert json.loads((saida / "resumo.json").read_text())["cortadas_por_janela"] == 0
