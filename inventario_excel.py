from io import BytesIO
from math import isfinite

import pandas as pd

from Funciones_Cartas import Normalizar_nombre


COLUMNAS = {
    "nombre": ("nombre", "carta", "name", "card name", "nombre de la carta"),
    "cantidad": ("cantidad", "quantity", "count", "stock", "qty"),
    "set": ("set", "set code", "código de edición"),
    "set_name": ("edición", "edicion", "set name", "expansión"),
    "collector_number": ("collector number", "número de colección", "número de coleccionista"),
    "scryfall_id": ("scryfall id",),
    "finish": ("acabado", "finish", "foil"),
    "idioma": ("idioma", "language", "lang"),
    "tipo": ("tipo", "type line"),
    "imagen_url": ("imagen", "imagen url", "image url"),
    "precio_ck": ("precio ck", "precio ck usd", "ck usd", "price ck"),
    "ubicacion": ("ubicación", "ubicacion", "location"),
}


def cargar_inventario_excel(contenido: bytes) -> list[dict]:
    """Lee el stock de la primera hoja y suma las filas de una misma variante."""
    return cargar_inventario(contenido, ".xlsx")


def cargar_inventario(contenido: bytes, formato: str) -> list[dict]:
    """Lee el inventario publicado y agrupa las copias de una misma variante."""
    if formato == ".csv":
        tabla = pd.read_csv(BytesIO(contenido), encoding="utf-8-sig", dtype=str, keep_default_na=False)
    elif formato == ".xlsx":
        tabla = pd.read_excel(BytesIO(contenido), engine="openpyxl", dtype=str, keep_default_na=False)
    else:
        raise ValueError(f"Formato de inventario no admitido: {formato}")
    tabla.columns = [
        Normalizar_nombre(str(columna).replace("_", " ")) for columna in tabla.columns
    ]
    columnas = {
        campo: next(
            (Normalizar_nombre(alias) for alias in aliases if Normalizar_nombre(alias) in tabla),
            None,
        )
        for campo, aliases in COLUMNAS.items()
    }
    if not (
        columnas["nombre"] or columnas["scryfall_id"]
        or (columnas["set"] and columnas["collector_number"])
    ):
        raise ValueError("Incluye Nombre, Scryfall ID o las columnas Set y Collector Number.")

    cartas = {}
    for numero, fila in enumerate(tabla.to_dict("records"), start=2):
        carta = {campo: fila.get(columna, "").strip() for campo, columna in columnas.items()}
        if not any(carta.values()):
            continue
        nombre = Normalizar_nombre(carta["nombre"])
        if not (nombre or carta["scryfall_id"] or (carta["set"] and carta["collector_number"])):
            raise ValueError(f"Fila {numero}: falta el nombre o la identificación de la carta.")

        try:
            cantidad = float(carta["cantidad"] or "1")
            if not cantidad.is_integer() or cantidad < 0:
                raise ValueError
        except ValueError:
            raise ValueError(f"Fila {numero}: la cantidad debe ser un entero no negativo.") from None
        if cantidad == 0:
            continue

        precio = carta["precio_ck"]
        if precio:
            try:
                precio = float(precio.removeprefix("$").strip().replace(",", "."))
                if not isfinite(precio) or precio < 0:
                    raise ValueError
            except ValueError:
                raise ValueError(f"Fila {numero}: el precio CK debe ser un número no negativo.") from None

        acabado = carta["finish"].casefold()
        acabados = {
            "": "", "no": "nonfoil", "false": "nonfoil", "0": "nonfoil",
            "sí": "foil", "si": "foil", "yes": "foil", "true": "foil", "1": "foil",
        }
        carta.update(
            nombre_normalizado=nombre,
            cantidad=int(cantidad),
            finish=acabados.get(acabado, acabado),
            precio_ck=precio if precio != "" else None,
        )
        campos_impresion = ("set", "set_name", "collector_number", "scryfall_id", "finish", "idioma", "ubicacion")
        clave = (nombre, *(carta[campo].casefold() for campo in campos_impresion), carta["precio_ck"])
        if clave in cartas:
            cartas[clave]["cantidad"] += carta["cantidad"]
        else:
            carta["id_inventario"] = repr(clave)
            cartas[clave] = carta

    return list(cartas.values())
