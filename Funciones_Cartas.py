import difflib
import re
import time
import unicodedata
from urllib.parse import urlparse

import requests


CABECERAS_MOXFIELD = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
}

CABECERAS_SCRYFALL = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "BuscadorCartasWombat/1.0",
}


# ============================================================
# LECTURA DE ARCHIVOS
# ============================================================


def BuscarCartas(nombre_archivo):
    cartas = []

    with open(nombre_archivo, "r", encoding="utf-8") as archivo:
        for linea in archivo:
            linea = linea.strip()

            if not linea or linea.startswith("#"):
                continue

            coincidencia = re.match(r"^\d+\s+(.+)$", linea)

            if coincidencia:
                cartas.append(coincidencia.group(1).strip())

    return cartas


def Leer_URLs(nombre_archivo):
    urls = []

    with open(nombre_archivo, "r", encoding="utf-8") as archivo:
        for numero_linea, linea in enumerate(archivo, start=1):
            linea = linea.strip()

            if not linea or linea.startswith("#"):
                continue

            if "moxfield.com/decks/" not in linea.casefold():
                print(
                    f"ADVERTENCIA: la línea {numero_linea} no parece ser "
                    f"una URL de Moxfield: {linea}"
                )
                continue

            urls.append(linea)

    return urls


def Obtener_ID_Moxfield(url):
    url = str(url).strip()

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    partes = [parte for parte in parsed.path.split("/") if parte]

    try:
        posicion_decks = partes.index("decks")
        return partes[posicion_decks + 1]
    except (ValueError, IndexError) as error:
        raise ValueError(
            f"No se pudo obtener el ID desde esta URL: {url}"
        ) from error


# ============================================================
# NORMALIZACIÓN DE NOMBRES
# ============================================================


def Normalizar_nombre(nombre):
    """
    Normalización tolerante para comparar nombres de cartas.

    - Unifica Unicode (NFKC).
    - Elimina caracteres invisibles.
    - Trata comillas y guiones tipográficos como equivalentes.
    - Ignora puntuación, mayúsculas y espacios repetidos.
    """
    texto = unicodedata.normalize("NFKC", str(nombre or ""))

    traducciones = str.maketrans(
        {
            "’": "'",
            "‘": "'",
            "`": "'",
            "´": "'",
            "‐": "-",
            "‑": "-",
            "‒": "-",
            "–": "-",
            "—": "-",
            "―": "-",
        }
    )
    texto = texto.translate(traducciones)

    texto = "".join(
        caracter
        for caracter in texto
        if unicodedata.category(caracter) != "Cf"
    )

    texto = texto.casefold()

    # La API de nombres de Scryfall también considera opcional la
    # puntuación. Hacemos lo mismo en la comparación local.
    texto = re.sub(r"[^\w\s]", " ", texto, flags=re.UNICODE)

    return " ".join(texto.split())


# Compatibilidad con el código anterior.
_normalizar_nombre = Normalizar_nombre


def _agregar_nombre_y_frente(variantes, valor):
    if not valor:
        return

    valor = str(valor).strip()
    normalizado = Normalizar_nombre(valor)

    if normalizado:
        variantes.add(normalizado)

    if "//" in valor:
        frente = valor.split("//", 1)[0].strip()
        frente_normalizado = Normalizar_nombre(frente)

        if frente_normalizado:
            variantes.add(frente_normalizado)


def _variantes_desde_objeto(objeto):
    variantes = set()

    if not isinstance(objeto, dict):
        return variantes

    for campo in (
        "name",
        "printed_name",
        "printedName",
        "flavor_name",
        "flavorName",
    ):
        _agregar_nombre_y_frente(variantes, objeto.get(campo))

    for cara in objeto.get("card_faces") or objeto.get("cardFaces") or []:
        if not isinstance(cara, dict):
            continue

        for campo in (
            "name",
            "printed_name",
            "printedName",
            "flavor_name",
            "flavorName",
        ):
            _agregar_nombre_y_frente(variantes, cara.get(campo))

    return variantes


def _nombres_originales_desde_objeto(objeto):
    """Devuelve los nombres visibles de una impresión sin normalizarlos."""
    nombres = set()

    if not isinstance(objeto, dict):
        return nombres

    for campo in (
        "name",
        "printed_name",
        "printedName",
        "flavor_name",
        "flavorName",
    ):
        valor = objeto.get(campo)

        if not valor:
            continue

        valor = str(valor).strip()
        nombres.add(valor)

        # En cartas de dos caras también se agrega solamente la cara frontal.
        if "//" in valor:
            nombres.add(valor.split("//", 1)[0].strip())

    for cara in objeto.get("card_faces") or objeto.get("cardFaces") or []:
        if not isinstance(cara, dict):
            continue

        nombres.update(_nombres_originales_desde_objeto(cara))

    return nombres


def _nombres_busqueda_carta(carta_moxfield, carta_scryfall=None):
    """
    Obtiene nombres canónicos, impresos, alternativos y caras conservando
    el texto original para poder mostrar sugerencias legibles en la interfaz.
    """
    nombres = _nombres_originales_desde_objeto(carta_moxfield)
    nombres.update(_nombres_originales_desde_objeto(carta_scryfall or {}))

    return sorted(nombre for nombre in nombres if Normalizar_nombre(nombre))


def _variantes_nombre_carta(carta_moxfield, carta_scryfall=None):
    """
    Obtiene los nombres normalizados usados para comparar búsquedas.
    """
    nombres = _nombres_busqueda_carta(carta_moxfield, carta_scryfall)
    return sorted({Normalizar_nombre(nombre) for nombre in nombres if nombre})


# ============================================================
# SCRYFALL: ENRIQUECIMIENTO POR ID ESTABLE
# ============================================================


def _consultar_scryfall_por_lotes(scryfall_ids):
    """
    Recupera datos oficiales de Scryfall en lotes de hasta 75 IDs.

    El resultado permite obtener flavor_name, printed_name, card_faces y
    oracle_id de la impresión exacta recibida desde Moxfield.
    """
    ids_unicos = list(dict.fromkeys(
        scryfall_id
        for scryfall_id in scryfall_ids
        if scryfall_id
    ))

    resultados = {}

    for inicio in range(0, len(ids_unicos), 75):
        lote = ids_unicos[inicio:inicio + 75]

        try:
            respuesta = requests.post(
                "https://api.scryfall.com/cards/collection",
                headers=CABECERAS_SCRYFALL,
                json={
                    "identifiers": [
                        {"id": scryfall_id}
                        for scryfall_id in lote
                    ]
                },
                timeout=30,
            )
            respuesta.raise_for_status()
            datos = respuesta.json()

            for carta in datos.get("data", []):
                scryfall_id = carta.get("id")

                if scryfall_id:
                    resultados[scryfall_id] = carta

        except (requests.RequestException, ValueError, TypeError):
            # Moxfield seguirá funcionando aunque Scryfall no responda.
            pass

        time.sleep(0.12)

    return resultados


# ============================================================
# PRECIOS E IMÁGENES
# ============================================================


def _convertir_precio(valor):
    if valor is None:
        return None

    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _obtener_precio_ck(carta, entrada):
    precios = carta.get("prices") or {}
    acabado = str(
        entrada.get("finish") or "nonfoil"
    ).strip().casefold()

    if acabado == "etched":
        clave = "ck_etched"
    elif acabado == "foil" or entrada.get("isFoil", False):
        clave = "ck_foil"
    else:
        clave = "ck"

    precio = _convertir_precio(precios.get(clave))

    if precio is None and clave != "ck":
        precio = _convertir_precio(precios.get("ck"))

    return precio


def _obtener_imagen_moxfield(carta):
    imagenes = (
        carta.get("image_uris")
        or carta.get("imageUris")
        or {}
    )

    if isinstance(imagenes, dict):
        for tamaño in ("normal", "large", "png", "small"):
            url = imagenes.get(tamaño)

            if url:
                return url

    for campo in ("imageUrl", "image_url", "imageUri", "image_uri"):
        url = carta.get(campo)

        if url:
            return url

    scryfall_id = carta.get("scryfall_id")

    if scryfall_id:
        return (
            f"https://api.scryfall.com/cards/{scryfall_id}"
            "?format=image&version=normal"
        )

    moxfield_id = carta.get("id")

    if moxfield_id:
        return (
            "https://assets.moxfield.net/cards/"
            f"card-{moxfield_id}-normal.webp"
        )

    return ""


# ============================================================
# MOXFIELD
# ============================================================


def Leer_Mazo_Moxfield(url):
    deck_id = Obtener_ID_Moxfield(url)
    api_url = f"https://api2.moxfield.com/v3/decks/all/{deck_id}"

    respuesta = requests.get(
        api_url,
        headers=CABECERAS_MOXFIELD,
        timeout=30,
    )

    if respuesta.status_code == 404:
        raise RuntimeError(
            "El mazo no existe, es privado o la URL está mal escrita."
        )

    if respuesta.status_code == 403:
        raise RuntimeError(
            "Moxfield bloqueó la consulta automática (error 403). "
            "Prueba nuevamente más tarde."
        )

    respuesta.raise_for_status()
    datos = respuesta.json()

    nombre_mazo = datos.get("name", deck_id)
    boards = datos.get("boards", {})
    entradas_crudas = []

    for nombre_tablero, tablero in boards.items():
        if not isinstance(tablero, dict):
            continue

        cartas_tablero = tablero.get("cards", {})

        if not isinstance(cartas_tablero, dict):
            continue

        for entrada in cartas_tablero.values():
            if not isinstance(entrada, dict):
                continue

            carta = entrada.get("card", {})

            if not isinstance(carta, dict):
                continue

            entradas_crudas.append((nombre_tablero, entrada, carta))

    datos_scryfall = _consultar_scryfall_por_lotes(
        carta.get("scryfall_id")
        for _, _, carta in entradas_crudas
    )

    cartas = []

    for nombre_tablero, entrada, carta in entradas_crudas:
        scryfall_id = carta.get("scryfall_id")
        carta_scryfall = datos_scryfall.get(scryfall_id, {})
        nombre = carta.get("name") or carta_scryfall.get("name")

        if not nombre:
            continue

        nombres_busqueda = _nombres_busqueda_carta(
            carta,
            carta_scryfall,
        )
        variantes = _variantes_nombre_carta(
            carta,
            carta_scryfall,
        )

        cartas.append(
            {
                "nombre": nombre,
                "nombre_normalizado": Normalizar_nombre(nombre),
                "nombres_busqueda": nombres_busqueda,
                "nombres_normalizados": variantes,
                "cantidad": entrada.get("quantity", 1),
                "tablero": nombre_tablero,
                "scryfall_id": scryfall_id,
                # ID estable de la impresión exacta seleccionada en Moxfield.
                # No debe sustituirse por una búsqueda por nombre.
                "impresion_exacta": bool(scryfall_id),
                "oracle_id": (
                    carta_scryfall.get("oracle_id")
                    or carta.get("oracle_id")
                    or carta.get("oracleId")
                ),
                "moxfield_id": carta.get("id", ""),
                # La edición se toma primero desde Scryfall usando el
                # scryfall_id que Moxfield entrega para esa impresión.
                # Esto evita que una búsqueda por nombre termine mostrando
                # otra edición de la misma carta. Si Scryfall no responde,
                # se conserva la información original de Moxfield.
                "set": (
                    carta_scryfall.get("set")
                    or carta.get("set", "")
                ),
                "set_name": (
                    carta_scryfall.get("set_name")
                    or carta.get("set_name", "")
                ),
                "collector_number": (
                    carta_scryfall.get("collector_number")
                    or carta.get("cn", "")
                ),
                "tipo": (
                    carta_scryfall.get("type_line")
                    or carta.get("type_line")
                    or carta.get("typeLine")
                    or ""
                ),
                "es_foil": entrada.get("isFoil", False),
                "finish": entrada.get("finish", "nonfoil"),
                "precio_ck": _obtener_precio_ck(carta, entrada),
                "cardkingdom_url": carta.get("cardKingdomUrl", ""),
                "imagen_url": _obtener_imagen_moxfield(carta),
            }
        )

    time.sleep(0.20)

    return {
        "nombre": nombre_mazo,
        "url": url,
        "cartas": cartas,
    }


def Comparar_cartas(mazo, cartas_buscadas):
    buscadas_normalizadas = {
        Normalizar_nombre(nombre): nombre
        for nombre in cartas_buscadas
    }

    encontradas = []

    for carta_mazo in mazo.get("cartas", []):
        variantes = set(
            carta_mazo.get("nombres_normalizados")
            or [carta_mazo.get("nombre_normalizado", "")]
        )

        coincidencias = variantes.intersection(
            buscadas_normalizadas.keys()
        )

        for nombre_normalizado in coincidencias:
            coincidencia = carta_mazo.copy()
            coincidencia["nombre_buscado"] = (
                buscadas_normalizadas[nombre_normalizado]
            )
            coincidencia["nombre_buscado_normalizado"] = (
                nombre_normalizado
            )
            encontradas.append(coincidencia)

    return encontradas

# ============================================================
# BÚSQUEDA PARA LA INTERFAZ WEB
# ============================================================


def _puntuar_coincidencia(consulta_normalizada, candidato_normalizado):
    """
    Asigna un puntaje menor a las coincidencias más relevantes.

    Prioridad:
    1. Coincidencia exacta.
    2. El nombre comienza por lo escrito.
    3. El texto aparece dentro del nombre.
    4. Todas las palabras aparecen, aunque estén separadas.
    5. Coincidencia aproximada para pequeñas diferencias de escritura.
    """
    if not consulta_normalizada or not candidato_normalizado:
        return None

    if candidato_normalizado == consulta_normalizada:
        return 0.0

    if candidato_normalizado.startswith(consulta_normalizada):
        return 10.0 + (len(candidato_normalizado) - len(consulta_normalizada)) / 1000

    posicion = candidato_normalizado.find(consulta_normalizada)
    if posicion >= 0:
        return 20.0 + posicion / 100 + len(candidato_normalizado) / 10000

    palabras = consulta_normalizada.split()
    if palabras and all(palabra in candidato_normalizado for palabra in palabras):
        return 30.0 + len(candidato_normalizado) / 10000

    similitud = difflib.SequenceMatcher(
        None,
        consulta_normalizada,
        candidato_normalizado,
    ).ratio()

    if similitud >= 0.68:
        return 100.0 - similitud * 50.0

    return None


def Buscar_sugerencias(inventario, consulta, limite=20):
    """
    Busca sugerencias dentro del inventario.

    Las sugerencias se agrupan por nombre visible, de manera que una carta
    disponible en varias ediciones no llena la lista con duplicados.
    """
    consulta_normalizada = Normalizar_nombre(consulta)

    if len(consulta_normalizada) < 3:
        return []

    sugerencias_por_clave = {}

    for carta in inventario:
        nombres = carta.get("nombres_busqueda") or [carta.get("nombre", "")]

        for nombre in nombres:
            candidato_normalizado = Normalizar_nombre(nombre)
            puntaje = _puntuar_coincidencia(
                consulta_normalizada,
                candidato_normalizado,
            )

            if puntaje is None:
                continue

            nombre_visible = carta.get("nombre_completo") or nombre
            clave = Normalizar_nombre(nombre_visible)
            actual = sugerencias_por_clave.get(clave)

            if actual is None or puntaje < actual["puntaje"]:
                sugerencias_por_clave[clave] = {
                    "nombre": nombre_visible,
                    "nombre_normalizado": clave,
                    "imagen_url": carta.get("imagen_url", ""),
                    "tipo": carta.get("tipo", ""),
                    "puntaje": puntaje,
                }

    sugerencias = sorted(
        sugerencias_por_clave.values(),
        key=lambda elemento: (
            elemento["puntaje"],
            elemento["nombre"].casefold(),
        ),
    )

    return sugerencias[:max(1, int(limite))]


def Filtrar_inventario_por_nombre(inventario, nombre):
    """Devuelve todas las impresiones que coinciden exactamente con un nombre."""
    nombre_normalizado = Normalizar_nombre(nombre)
    resultados = []

    for carta in inventario:
        variantes = set(
            carta.get("nombres_normalizados")
            or [carta.get("nombre_normalizado", "")]
        )

        if nombre_normalizado in variantes:
            coincidencia = carta.copy()
            coincidencia["nombre_buscado"] = nombre
            coincidencia["nombre_buscado_normalizado"] = nombre_normalizado
            resultados.append(coincidencia)

    return resultados

