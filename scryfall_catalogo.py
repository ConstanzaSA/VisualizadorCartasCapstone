from time import sleep

import requests

from Funciones_Cartas import Normalizar_nombre, _nombres_originales_desde_objeto


def identificador_scryfall(carta: dict) -> tuple[str, ...]:
    if carta["scryfall_id"]:
        return ("id", carta["scryfall_id"].casefold())
    if carta["set"] and carta["collector_number"]:
        return ("set", carta["set"].casefold(), carta["collector_number"].casefold())
    return ()


def consultar_impresiones(identificadores: tuple[tuple[str, ...], ...]) -> dict:
    """Devuelve las impresiones identificadas en un lote de hasta 75 cartas."""
    consulta = [
        {"id": clave[1]} if clave[0] == "id"
        else {"set": clave[1], "collector_number": clave[2]}
        for clave in identificadores
    ]
    try:
        respuesta = requests.post(
            "https://api.scryfall.com/cards/collection",
            headers={
                "Accept": "application/json",
                "User-Agent": "TiendaDeMagicUC/1.0",
            },
            json={"identifiers": consulta},
            timeout=8,
        )
        respuesta.raise_for_status()
        datos = respuesta.json()
    finally:
        sleep(0.12)

    impresiones = {}
    for carta in datos["data"]:
        clave_id = ("id", carta["id"].casefold())
        clave_set = ("set", carta["set"].casefold(), carta["collector_number"].casefold())
        impresiones[clave_id] = carta
        if clave_set not in impresiones or carta.get("lang") == "en":
            impresiones[clave_set] = carta
    return impresiones


def completar_carta(carta: dict, impresion: dict) -> dict:
    caras = impresion.get("card_faces") or []
    imagen = (impresion.get("image_uris") or {}).get("normal")
    if not imagen and caras:
        imagen = (caras[0].get("image_uris") or {}).get("normal")

    colores = impresion.get("colors")
    if colores is None and caras and all("colors" in cara for cara in caras):
        colores = sorted({color for cara in caras for color in cara["colors"]})

    nombre = carta["nombre"] or impresion["name"]
    nombres = _nombres_originales_desde_objeto(impresion) | {nombre}
    return {
        **carta,
        "nombre": nombre,
        "nombre_completo": impresion["name"] if len(caras) > 1 else "",
        "nombre_normalizado": Normalizar_nombre(nombre),
        "nombres_busqueda": sorted(nombres),
        "nombres_normalizados": sorted({Normalizar_nombre(n) for n in nombres}),
        "set_name": impresion.get("set_name") or carta["set_name"] or carta["set"],
        "imagen_url": imagen or carta["imagen_url"],
        "tipo": impresion.get("type_line") or carta["tipo"],
        "colores": colores,
    }
