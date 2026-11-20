import gzip
import json
import re
from io import BytesIO
from math import isfinite

import ijson
import requests


BASE_URL = "https://mtgjson.com/api/v5"
ACABADOS = {"nonfoil": "normal", "foil": "foil", "etched": "etched"}


def cargar_precios_ck() -> dict[str, dict[str, float]]:
    """Indexa precios de venta en USD sin cargar todo el JSON en memoria."""
    precios = {}
    with requests.get(f"{BASE_URL}/AllPricesToday.json.gz", timeout=(5, 25)) as respuesta:
        respuesta.raise_for_status()
        with gzip.GzipFile(fileobj=BytesIO(respuesta.content)) as archivo:
            for uuid, datos in ijson.kvitems(archivo, "data", use_float=True):
                ck = datos.get("paper", {}).get("cardkingdom", {})
                if ck.get("currency") != "USD":
                    continue
                acabados = {}
                for acabado, valores in ck.get("retail", {}).items():
                    if acabado in ACABADOS.values() and valores:
                        precio = valores[max(valores)]
                        if isinstance(precio, (int, float)) and isfinite(precio) and precio > 0:
                            acabados[acabado] = float(precio)
                if acabados:
                    precios[uuid] = acabados
    return precios


def cargar_uuids_edicion(codigo: str) -> dict[str, str]:
    """Relaciona IDs exactos de Scryfall con los UUID de precios de MTGJSON."""
    codigo = codigo.upper()
    if not re.fullmatch(r"[A-Z0-9]+", codigo):
        return {}
    with requests.get(f"{BASE_URL}/{codigo}.json.gz", timeout=(5, 12)) as respuesta:
        if respuesta.status_code == 404:
            return {}
        respuesta.raise_for_status()
        with gzip.GzipFile(fileobj=BytesIO(respuesta.content)) as archivo:
            cartas = json.load(archivo)["data"]["cards"]
    return {
        carta["identifiers"]["scryfallId"].casefold(): carta["uuid"]
        for carta in cartas
        if carta.get("side") != "b" and carta.get("identifiers", {}).get("scryfallId")
    }
