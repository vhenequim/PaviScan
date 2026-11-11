"""Base publica UAV-PDD2023 (fotos de drone anotadas): resumo e exemplos para o painel.

Hoje a base so ilustra o proximo passo do projeto: treinar um detector que distingue os
tipos de defeito. Nada aqui entra no calculo dos levantamentos.
Referencia: Yan e Zhang, "UAV-PDD2023", Zenodo, 2023 (https://zenodo.org/records/8429208).
"""

from __future__ import annotations

import json
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2

ROTULO = {
    "Transverse crack": "Trinca transversal",
    "Longitudinal crack": "Trinca longitudinal",
    "Oblique crack": "Trinca oblíqua",
    "Alligator crack": "Couro de jacaré",
    "Repair": "Remendo",
    "Pothole": "Panela",
}
COR_BGR = {
    "Pothole": (57, 40, 214),
    "Repair": (48, 123, 238),
    "Alligator crack": (60, 178, 227),
}


def _sem_acento(texto: str) -> str:
    """As fontes do OpenCV so desenham ASCII."""
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()


def _objetos(xml: Path) -> list[tuple[str, int, int, int, int]]:
    saida = []
    for o in ET.parse(xml).getroot().findall("object"):
        cx = o.find("bndbox")
        caixa = [int(float(cx.findtext(k))) for k in ("xmin", "ymin", "xmax", "ymax")]
        saida.append((o.findtext("name"), *caixa))
    return saida


def resumo(pasta: Path) -> dict | None:
    """Numero de imagens e de defeitos anotados por classe. None se a base nao esta na pasta."""
    pasta = Path(pasta)
    anot = pasta / "Annotations"
    if not anot.is_dir():
        return None
    cache = pasta / "resumo.json"
    if cache.is_file():
        return json.loads(cache.read_text())
    por_classe: dict[str, int] = {}
    n = 0
    for xml in anot.glob("*.xml"):
        n += 1
        for nome, *_ in _objetos(xml):
            por_classe[nome] = por_classe.get(nome, 0) + 1
    r = {"imagens": n, "defeitos": sum(por_classe.values()), "por_classe": por_classe}
    cache.write_text(json.dumps(r))
    return r


def exemplos(pasta: Path, quantos: int = 4, lado_px: int = 900) -> list[Path]:
    """Fotos da base com as caixas anotadas desenhadas, gravadas em `<pasta>/exemplos/`.

    Escolhe as fotos disponiveis com mais panelas anotadas, uma por cena.
    """
    pasta = Path(pasta)
    destino = pasta / "exemplos"
    prontos = sorted(destino.glob("*.jpg")) if destino.is_dir() else []
    if len(prontos) >= quantos:
        return prontos[:quantos]
    candidatos = []
    for jpg in (pasta / "JPEGImages").glob("*.jpg"):
        xml = pasta / "Annotations" / (jpg.stem + ".xml")
        if xml.is_file():
            objetos = _objetos(xml)
            candidatos.append((sum(o[0] == "Pothole" for o in objetos), len(objetos), jpg, objetos))
    destino.mkdir(exist_ok=True)
    vistos, saida = set(), []
    for _, _, jpg, objetos in sorted(candidatos, key=lambda c: (-c[0], -c[1], c[2].name)):
        cena = jpg.stem.split("_", 1)[-1]  # o prefixo (lr, tb, r180) e so o espelhamento
        if cena in vistos:
            continue
        vistos.add(cena)
        img = cv2.imread(str(jpg))
        for nome, x0, y0, x1, y1 in objetos:
            cor = COR_BGR.get(nome, (255, 255, 255))
            cv2.rectangle(img, (x0, y0), (x1, y1), cor, 6)
            cv2.putText(
                img, _sem_acento(ROTULO.get(nome, nome)), (x0, max(34, y0 - 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.3, cor, 3, cv2.LINE_AA,
            )  # fmt: skip
        f = lado_px / max(img.shape[:2])
        img = cv2.resize(
            img, (int(img.shape[1] * f), int(img.shape[0] * f)), interpolation=cv2.INTER_AREA
        )
        arq = destino / f"{jpg.stem}.jpg"
        cv2.imwrite(str(arq), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        saida.append(arq)
        if len(saida) >= quantos:
            break
    return saida
