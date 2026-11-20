from __future__ import annotations

import base64
import difflib
import hashlib
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import streamlit as st
from ijson import JSONError

# El componente streamlit-searchbox permite actualizar las sugerencias
# mientras la persona escribe, sin tener que presionar Enter.
try:
    from streamlit_searchbox import st_searchbox
except ImportError:
    # La aplicación conserva un buscador básico si todavía no se agregó
    # streamlit-searchbox al archivo requirements.txt.
    st_searchbox = None

# Las funciones básicas existen en todas las versiones del proyecto.
from Funciones_Cartas import Comparar_cartas
from inventario_excel import cargar_inventario
from precios_mtgjson import ACABADOS, cargar_precios_ck, cargar_uuids_edicion
from scryfall_catalogo import completar_carta, consultar_impresiones, identificador_scryfall

from requests import RequestException

# Compatibilidad: si GitHub conserva temporalmente una versión anterior de
# Funciones_Cartas.py, la aplicación sigue iniciando y define localmente las
# funciones nuevas de búsqueda. Cuando el archivo actualizado esté disponible,
# se utilizarán automáticamente sus implementaciones.
try:
    from Funciones_Cartas import Normalizar_nombre
except ImportError:
    def Normalizar_nombre(nombre):
        texto = unicodedata.normalize("NFKC", str(nombre or ""))
        texto = texto.translate(
            str.maketrans(
                {
                    "’": "'",
                    "‘": "'",
                    "`": "'",
                    "´": "'",
                    "‐": "-",
                    "‑": "-",
                    "–": "-",
                    "—": "-",
                }
            )
        )
        texto = "".join(
            caracter
            for caracter in texto
            if unicodedata.category(caracter) != "Cf"
        )
        texto = re.sub(r"[^\w\s]", " ", texto.casefold())
        return " ".join(texto.split())

try:
    from Funciones_Cartas import (
        Buscar_sugerencias,
        Filtrar_inventario_por_nombre,
    )
except ImportError:
    def _puntuar_coincidencia(consulta, candidato):
        if not consulta or not candidato:
            return None
        if candidato == consulta:
            return 0.0
        if candidato.startswith(consulta):
            return 10.0 + (len(candidato) - len(consulta)) / 1000
        posicion = candidato.find(consulta)
        if posicion >= 0:
            return 20.0 + posicion / 100
        palabras = consulta.split()
        if palabras and all(palabra in candidato for palabra in palabras):
            return 30.0 + len(candidato) / 10000
        similitud = difflib.SequenceMatcher(None, consulta, candidato).ratio()
        if similitud >= 0.68:
            return 100.0 - similitud * 50.0
        return None

    def Buscar_sugerencias(inventario, consulta, limite=20):
        consulta_normalizada = Normalizar_nombre(consulta)
        if len(consulta_normalizada) < 3:
            return []

        sugerencias = {}
        for carta in inventario:
            nombres = (
                carta.get("nombres_busqueda")
                or carta.get("nombres_originales")
                or [carta.get("nombre", "")]
            )
            for nombre in nombres:
                candidato = Normalizar_nombre(nombre)
                puntaje = _puntuar_coincidencia(
                    consulta_normalizada,
                    candidato,
                )
                if puntaje is None:
                    continue
                actual = sugerencias.get(candidato)
                if actual is None or puntaje < actual["puntaje"]:
                    sugerencias[candidato] = {
                        "nombre": nombre,
                        "nombre_normalizado": candidato,
                        "imagen_url": carta.get("imagen_url", ""),
                        "tipo": carta.get("tipo", ""),
                        "puntaje": puntaje,
                    }

        return sorted(
            sugerencias.values(),
            key=lambda item: (item["puntaje"], item["nombre"].casefold()),
        )[:max(1, int(limite))]

    def Filtrar_inventario_por_nombre(inventario, nombre):
        nombre_normalizado = Normalizar_nombre(nombre)
        resultados = []
        for carta in inventario:
            variantes = set(
                carta.get("nombres_normalizados")
                or [Normalizar_nombre(carta.get("nombre", ""))]
            )
            if nombre_normalizado in variantes:
                coincidencia = carta.copy()
                coincidencia["nombre_buscado"] = nombre
                coincidencia["nombre_buscado_normalizado"] = (
                    nombre_normalizado
                )
                resultados.append(coincidencia)
        return resultados


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
ARCHIVO_CSV = BASE_DIR / "Datos" / "inventario.csv"
ARCHIVO_EXCEL = ARCHIVO_CSV.with_suffix(".xlsx")
LOGO_PATH = BASE_DIR / "assets" / "logo_magic.png"
NUMERO_WHATSAPP = "56964463010"

MENSAJE_WHATSAPP_BASE = (
    "Hola, vengo desde Tienda de Magic UC. "
    "Quisiera consultar por la disponibilidad de mi carro:"
)


def whatsapp_url_carrito(carro: dict[str, dict[str, Any]]) -> str:
    """Construye un mensaje corto y punteado con el contenido del carrito."""
    lineas = [MENSAJE_WHATSAPP_BASE]

    total = 0

    for item in carro.values():
        cantidad = int(item.get("Cantidad", 0))
        if cantidad <= 0:
            continue

        precio_unitario = item.get("Precio CLP")
        subtotal = (
            round(float(precio_unitario) * cantidad)
            if precio_unitario is not None
            else None
        )

        if subtotal is not None:
            total += subtotal
            lineas.append(
                f"• {cantidad}x {item.get('Carta', '')} — "
                f"{precio_clp_texto(subtotal)}"
            )
        else:
            lineas.append(
                f"• {cantidad}x {item.get('Carta', '')} — "
                "precio por confirmar"
            )

    lineas.append("")
    lineas.append(f"Subtotal: {precio_clp_texto(total)}")

    mensaje = "\n".join(lineas)
    return (
        f"https://wa.me/{NUMERO_WHATSAPP}"
        f"?text={quote(mensaje)}"
    )

COLOR_MORADO_OSCURO = "#302B4F"
COLOR_MORADO_MEDIO = "#6E5A87"
COLOR_LILA = "#AA8DAF"
COLOR_GRIS_VIOLETA = "#9292AA"
COLOR_FONDO = "#0D0C11"
COLOR_PANEL = "#191620"
COLOR_TEXTO = "#F7F4FA"
COLOR_TEXTO_SECUNDARIO = "#C5BECC"
COLOR_BLANCO = "#FFFFFF"

# Conversión fija usada por la tienda.
VALOR_CK_CLP = 850

st.set_page_config(
    page_title="Tienda de Magic UC",
    page_icon="🃏",
    layout="wide",
)


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================


def normalizar_nombre(nombre: str) -> str:
    """Usa la misma normalización tolerante del módulo de cartas."""
    return Normalizar_nombre(nombre)


def precio_usd_texto(valor: Any) -> str:
    """
    Formatea el precio Card Kingdom usando coma como separador decimal.

    Ejemplo:
        2.79 -> $2,79
    """
    if valor is None or pd.isna(valor):
        return "Precio por confirmar"

    return f"${float(valor):,.2f}".replace(",", "X").replace(".", ",").replace(
        "X",
        ".",
    )


def precio_clp_texto(valor: Any) -> str:
    if valor is None or pd.isna(valor):
        return "Precio por confirmar"
    return f"${float(valor):,.0f}".replace(",", ".")


def limpiar_nombre_lista(nombre: str) -> str:
    """
    Limpia nombres pegados desde Manabox, Moxfield y listas de texto.

    Formatos admitidos:
        1 Beast Whisperer (CLU) 158
        3 Aclazotz, Deepest Betrayal / Temple of the Dead (LCI) 88
        2 Ad Nauseam
        1x Sol Ring
        Sol Ring
        1 Sol Ring [CMM] 396
        1 Sol Ring (CMM) 396 *F*

    Solo se elimina información técnica ubicada al final: edición,
    número de coleccionista y marcadores de acabado.
    """
    nombre = str(nombre or "").strip()

    # Marcadores de acabado comunes al final.
    nombre = re.sub(
        r"\s+(?:\*F\*|\*E\*|\[foil\]|\(foil\)|foil|etched)\s*$",
        "",
        nombre,
        flags=re.IGNORECASE,
    ).strip()

    # "Nombre (SET) 123", "(SET) 123a" o "(SET)".
    nombre = re.sub(
        r"\s+\([A-Za-z0-9]{2,8}\)"
        r"(?:\s+[A-Za-z0-9][A-Za-z0-9\-]*)?\s*$",
        "",
        nombre,
    ).strip()

    # "Nombre [SET] 123".
    nombre = re.sub(
        r"\s+\[[A-Za-z0-9]{2,8}\]"
        r"(?:\s+[A-Za-z0-9][A-Za-z0-9\-]*)?\s*$",
        "",
        nombre,
    ).strip()

    # Número de coleccionista aislado al final.
    nombre = re.sub(
        r"\s+\d+[A-Za-z]?\s*$",
        "",
        nombre,
    ).strip()

    # Uniforma cartas de dos caras.
    nombre = re.sub(r"\s+/{1,2}\s+", " // ", nombre)

    return nombre


def parsear_lista_cartas(texto: str) -> list[dict[str, Any]]:
    """
    Convierte listas de Manabox, Moxfield o texto simple en líneas buscables.

    Admite:
    - "1 Beast Whisperer (CLU) 158"
    - "3 Aclazotz, Deepest Betrayal / Temple of the Dead (LCI) 88"
    - "2 Ad Nauseam"
    - "1x Sol Ring"
    - "Sol Ring"

    Las líneas repetidas se conservan para que el contador represente
    exactamente las líneas válidas pegadas por el usuario.
    """
    cartas: list[dict[str, Any]] = []

    for numero_linea, linea_original in enumerate(
        str(texto).splitlines(),
        start=1,
    ):
        linea = linea_original.strip()

        if not linea or linea.startswith("#"):
            continue

        # Ignora encabezados habituales de exportaciones.
        if re.fullmatch(
            r"(?:deck|mainboard|sideboard|commander|commanders|"
            r"considering|maybeboard|companions?)\s*:?",
            linea,
            flags=re.IGNORECASE,
        ):
            continue

        # "2 Carta", "2x Carta", "2 X Carta" y "SB: 2 Carta".
        coincidencia = re.match(
            r"^\s*(?:(?:SB|SIDEBOARD)\s*:\s*)?"
            r"(\d+)\s*[xX]?\s+(.+?)\s*$",
            linea,
            flags=re.IGNORECASE,
        )

        if coincidencia:
            cantidad = int(coincidencia.group(1))
            nombre_crudo = coincidencia.group(2).strip()
        else:
            cantidad = 1
            nombre_crudo = linea

        nombre = limpiar_nombre_lista(nombre_crudo)

        if cantidad <= 0 or not normalizar_nombre(nombre):
            continue

        cartas.append(
            {
                "nombre": nombre,
                "Cantidad_buscada": cantidad,
                "Numero_linea": numero_linea,
                "Texto_original": linea_original.strip(),
            }
        )

    return cartas


@st.cache_data(show_spinner=False, max_entries=2)
def cargar_inventario_compartido(contenido: bytes, formato: str) -> list[dict[str, Any]]:
    return cargar_inventario(contenido, formato)


@st.cache_data(show_spinner=False, ttl="24h", max_entries=128)
def consultar_impresiones_compartidas(identificadores: tuple[tuple[str, ...], ...]) -> dict:
    return consultar_impresiones(identificadores)


@st.cache_resource(ttl="1h", show_spinner=False)
def precios_ck_compartidos() -> dict[str, dict[str, float]]:
    try:
        return cargar_precios_ck()
    except (RequestException, OSError, ValueError, JSONError):
        logging.getLogger(__name__).warning("No se pudo cargar el archivo de precios de MTGJSON.", exc_info=True)
        return {}


@st.cache_resource(ttl="1h", max_entries=256, show_spinner=False)
def uuids_mtgjson_compartidos(edicion: str) -> dict[str, str]:
    try:
        return cargar_uuids_edicion(edicion)
    except (RequestException, OSError, ValueError, KeyError):
        logging.getLogger(__name__).warning("No se pudo cargar la edición %s de MTGJSON.", edicion, exc_info=True)
        return {}


def completar_inventario(inventario: list[dict[str, Any]]) -> list[dict[str, Any]]:
    identificadores = sorted({identificador_scryfall(carta) for carta in inventario} - {()})
    impresiones = {}
    for inicio in range(0, len(identificadores), 75):
        lote = tuple(identificadores[inicio:inicio + 75])
        try:
            impresiones.update(consultar_impresiones_compartidas(lote))
        except (RequestException, ValueError, KeyError, TypeError):
            break

    ediciones = {
        impresion["set"]
        for carta in inventario
        if carta["finish"] in ACABADOS
        if (impresion := impresiones.get(identificador_scryfall(carta)))
    }
    precios = precios_ck_compartidos() if ediciones else {}
    uuids = {edicion: uuids_mtgjson_compartidos(edicion) for edicion in sorted(ediciones)} if precios else {}

    resultado = []
    pendientes = 0
    for carta in inventario:
        clave = identificador_scryfall(carta)
        impresion = impresiones.get(clave)
        if impresion:
            completada = completar_carta(carta, impresion)
            uuid = uuids.get(impresion["set"], {}).get(impresion["id"].casefold())
            precio = precios.get(uuid, {}).get(ACABADOS.get(carta["finish"]))
            if precio is not None:
                completada["precio_ck"] = precio
            resultado.append(completada)
            continue
        if clave:
            pendientes += 1
        resultado.append({
            **carta,
            "nombre": carta["nombre"] or " ".join(clave[1:]),
            "set_name": carta["set_name"] or carta["set"],
        })
    if pendientes:
        st.warning(f"Scryfall no devolvió los datos de {pendientes} entradas del inventario.")
    return resultado


def idioma_visible(idioma: str) -> str:
    return {"es": "Español", "en": "Inglés"}.get(idioma.casefold(), idioma)


def almacenamiento_carta(carta: dict[str, Any]) -> str:
    if carta.get("ubicacion"):
        return carta["ubicacion"]

    color = color_catalogo(carta)
    if color == "Sin datos de color":
        return "Por definir"
    if color in ("Multicolor", "Incoloro"):
        return "Multicolor / Incoloro"
    return color


def preparar_resultado(
    carta: dict[str, Any],
    nombre_solicitado: str | None = None,
) -> dict[str, Any]:
    """Convierte una entrada del inventario en una tarjeta de resultados."""
    precio_ck = carta.get("precio_ck")
    precio_clp = (
        round(float(precio_ck) * VALOR_CK_CLP)
        if precio_ck is not None
        else None
    )

    identificador = hashlib.sha1(
        carta["id_inventario"].encode("utf-8")
    ).hexdigest()

    return {
        "ID item": identificador,
        "Imagen": carta.get("imagen_url", ""),
        "Carta": carta.get("nombre_completo") or nombre_solicitado or carta.get("nombre", ""),
        "Tipo": carta.get("tipo", ""),
        "Almacenamiento": almacenamiento_carta(carta),
        "Idioma": idioma_visible(carta.get("idioma", "")),
        "Cantidad disponible": int(carta.get("cantidad") or 0),
        "Edición": carta.get("set_name", ""),
        "Acabado": carta.get("finish", "nonfoil"),
        "Precio CK": precio_ck,
        "Precio CLP": precio_clp,
    }


def buscar_lista_en_inventario(
    cartas_buscadas: list[dict[str, Any]],
    inventario: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Ejecuta la búsqueda secundaria conservando el conteo por línea."""
    nombres_unicos: dict[str, str] = {}

    for carta in cartas_buscadas:
        clave = normalizar_nombre(carta["nombre"])
        nombres_unicos.setdefault(clave, carta["nombre"])

    coincidencias = Comparar_cartas(
        {"cartas": inventario},
        list(nombres_unicos.values()),
    )

    resultados_por_item = {}
    for carta in coincidencias:
        fila = preparar_resultado(
            carta,
            carta.get("nombre_buscado") or carta.get("nombre"),
        )
        resultados_por_item.setdefault(fila["ID item"], fila)
    resultados = list(resultados_por_item.values())

    nombres_encontrados = {
        normalizar_nombre(
            carta.get("nombre_buscado") or carta.get("nombre", "")
        )
        for carta in coincidencias
    }

    no_encontradas = [
        carta["nombre"]
        for carta in cartas_buscadas
        if normalizar_nombre(carta["nombre"])
        not in nombres_encontrados
    ]

    resultados.sort(
        key=lambda fila: (
            fila["Carta"].casefold(),
            fila["Edición"].casefold(),
            fila["Acabado"].casefold(),
        )
    )

    return resultados, no_encontradas


def opciones_autocompletado(
    termino: str,
    inventario: list[dict[str, Any]],
) -> list[tuple[str, dict[str, str]]]:
    """
    Opciones compactas mostradas bajo el buscador.

    El componente llama esta función automáticamente después de cada cambio.
    No devuelve resultados hasta que haya al menos tres caracteres útiles.
    """
    termino_normalizado = normalizar_nombre(termino)

    if len(termino_normalizado) < 3:
        return []

    sugerencias = Buscar_sugerencias(
        inventario,
        termino,
        limite=12,
    )

    opciones = [
        (
            f'Ver todas las sugerencias para "{termino.strip()}"',
            {
                "accion": "mostrar_sugerencias",
                "termino": termino.strip(),
            },
        )
    ]

    opciones.extend(
        (
            sugerencia["nombre"],
            {
                "accion": "seleccionar_carta",
                "nombre": sugerencia["nombre"],
                "nombre_normalizado": sugerencia["nombre_normalizado"],
            },
        )
        for sugerencia in sugerencias
    )

    return opciones


def resultados_desde_sugerencias(
    sugerencias: list[dict[str, Any]],
    inventario: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Muestra una impresión representativa por nombre mientras se escribe.

    Al escoger una opción del desplegable se reemplazan estas vistas previas
    por todas las impresiones disponibles de la carta seleccionada.
    """
    resultados: list[dict[str, Any]] = []

    for sugerencia in sugerencias:
        coincidencias = Filtrar_inventario_por_nombre(
            inventario,
            sugerencia["nombre"],
        )

        if not coincidencias:
            continue

        coincidencias.sort(
            key=lambda carta: (
                carta.get("precio_ck") is None,
                float(carta.get("precio_ck") or 10**9),
                str(carta.get("set_name", "")).casefold(),
            )
        )

        resultados.append(
            preparar_resultado(
                coincidencias[0],
                sugerencia["nombre"],
            )
        )

    return resultados


def guardar_busqueda(
    resultados: list[dict[str, Any]],
    no_encontradas: list[str],
    total_buscado: int,
    total_encontrado: int,
    tipo_busqueda: str,
) -> None:
    """Guarda los resultados y abre siempre la vista de cartas."""
    st.session_state["resultados"] = resultados
    st.session_state["no_encontradas"] = no_encontradas
    st.session_state["total_buscado"] = total_buscado
    st.session_state["total_encontrado"] = total_encontrado
    st.session_state["tipo_busqueda_realizada"] = tipo_busqueda
    st.session_state["vista_resultados"] = "Vista de cartas"


def selector_botones(
    clave: str,
    opciones: list[str],
    predeterminado: str,
) -> str:
    """
    Crea pestañas visuales usando botones para poder cambiar su estado
    desde Python, algo que st.tabs no permite controlar directamente.
    """
    if st.session_state.get(clave) not in opciones:
        st.session_state[clave] = predeterminado

    # Contenedor identificable para controlar el comportamiento responsive.
    with st.container(key=f"{clave}_navigation"):
        columnas = st.columns(len(opciones), gap="small")

        for indice, opcion in enumerate(opciones):
            activo = st.session_state[clave] == opcion
            tipo = "primary" if activo else "secondary"

            if columnas[indice].button(
                opcion,
                key=f"{clave}_{indice}",
                type=tipo,
                use_container_width=True,
            ):
                if not activo:
                    st.session_state[clave] = opcion
                    st.rerun()

    return st.session_state[clave]


def tarjeta_metrica(
    titulo: str,
    valor: str,
    subtitulo: str = "",
    clase_extra: str = "",
) -> None:
    """
    Widget visual reutilizable para los indicadores.

    `clase_extra` permite mostrar una copia diferente en escritorio y
    teléfono sin alterar el orden de la interfaz de computador.
    """
    clases = "metric-card"

    if clase_extra:
        clases += f" {clase_extra}"

    subtitulo_html = f'<div class="metric-subtitle">{subtitulo}</div>' if subtitulo else ""
    html = (
        f'<div class="{clases}">'
        f'<div class="metric-title">{titulo}</div>'
        f'<div class="metric-value">{valor}</div>'
        f"{subtitulo_html}"
        "</div>"
    )
    st.markdown(html, unsafe_allow_html=True)


def agregar_al_carro(fila: dict[str, Any]) -> None:
    """Agrega una unidad sin superar el stock disponible."""
    carro = st.session_state.setdefault("carro", {})
    item_id = fila["ID item"]
    disponible = max(0, int(fila.get("Cantidad disponible") or 0))

    if disponible <= 0:
        st.warning("Esta impresión no tiene unidades disponibles.")
        return

    clave_cantidad = f"cantidad_carro_{item_id}"

    if item_id not in carro:
        carro[item_id] = {
            **fila,
            "Cantidad": 1,
        }
        st.session_state[clave_cantidad] = 1
        # Cambia la clave del editor para que la tabla incorpore la fila nueva.
        st.session_state["carro_editor_version"] = (
            st.session_state.get("carro_editor_version", 0) + 1
        )
        st.toast(f"{fila['Carta']} fue añadida al carro.")
        return

    cantidad_actual = int(carro[item_id].get("Cantidad", 1))

    if cantidad_actual >= disponible:
        st.warning("Ya agregaste todas las unidades disponibles.")
        return

    nueva_cantidad = cantidad_actual + 1
    carro[item_id]["Cantidad"] = nueva_cantidad
    st.session_state[clave_cantidad] = nueva_cantidad
    st.session_state["carro_editor_version"] = (
        st.session_state.get("carro_editor_version", 0) + 1
    )
    st.toast(f"Se agregó otra unidad de {fila['Carta']}.")


def ajustar_cantidad_carro(
    item_id: str,
    variacion: int,
) -> None:
    """
    Aumenta o disminuye una impresión del carro.

    - El botón + nunca supera el stock disponible.
    - El botón − elimina la fila cuando la cantidad baja desde 1 a 0.
    """
    carro = st.session_state.setdefault("carro", {})

    if item_id not in carro:
        return

    item = carro[item_id]
    cantidad_actual = int(item.get("Cantidad", 1))
    stock_maximo = max(
        0,
        int(item.get("Cantidad disponible") or 0),
    )

    cantidad_nueva = cantidad_actual + int(variacion)

    if cantidad_nueva <= 0:
        del carro[item_id]
    else:
        carro[item_id]["Cantidad"] = min(
            cantidad_nueva,
            stock_maximo,
        )

    st.session_state["carro_editor_version"] = (
        st.session_state.get("carro_editor_version", 0) + 1
    )


def dataframe_compra() -> pd.DataFrame:
    """Genera el detalle final de compra, sin columnas técnicas."""
    filas = []

    for item in st.session_state.get("carro", {}).values():
        cantidad = int(item.get("Cantidad", 0))
        precio_unitario = item.get("Precio CLP")

        if cantidad <= 0:
            continue

        subtotal = (
            round(float(precio_unitario) * cantidad)
            if precio_unitario is not None
            else None
        )

        filas.append(
            {
                "Carta": item.get("Carta", ""),
                "Cantidad": cantidad,
                "Edición": item.get("Edición", ""),
                "Idioma": idioma_visible(item.get("Idioma", "")),
                "Almacenamiento": item.get("Almacenamiento") or item.get("Ubicación") or "Por definir",
                "Acabado": item.get("Acabado", ""),
                "Precio unitario CLP": precio_unitario,
                "Subtotal CLP": subtotal,
            }
        )

    return pd.DataFrame(filas)


# ============================================================
# ESTILOS DE LA INTERFAZ
# ============================================================

st.markdown(
    f"""
    <style>
        html,
        body,
        #root,
        [data-testid="stAppViewContainer"],
        [data-testid="stApp"] {{
            background-color: {COLOR_FONDO} !important;
        }}

        [data-testid="stHeader"] {{
            background-color: {COLOR_FONDO} !important;
            border: none !important;
            box-shadow: none !important;
        }}

        [data-testid="stDecoration"] {{
            display: none !important;
        }}

        .stApp {{
            background: {COLOR_FONDO};
            color: {COLOR_TEXTO};
        }}

        .block-container {{
            max-width: 1360px;
            padding-top: 1rem;
            padding-bottom: 3rem;
        }}

        p, span, label {{
            color: {COLOR_TEXTO};
        }}

        /* Encabezado principal mostrado en la primera página del PDF. */
        .buscador-hero {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 2rem;
            min-height: 160px;
            padding: 1.6rem 2rem;
            margin-bottom: 1.4rem;
            border: 1px solid rgba(170, 141, 175, 0.20);
            border-radius: 22px;
            background: linear-gradient(
                115deg,
                {COLOR_MORADO_OSCURO} 0%,
                #44365F 65%,
                {COLOR_MORADO_MEDIO} 100%
            );
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.30);
            overflow: hidden;
        }}

        .buscador-hero h1 {{
            margin: 0;
            color: {COLOR_BLANCO};
            font-size: clamp(2rem, 4vw, 3.2rem);
            line-height: 1.05;
            font-weight: 850;
        }}

        /* Oculta solamente los enlaces automáticos internos de encabezado. */
        .buscador-hero a[href^="#"] {{
            display: none !important;
        }}

        .hero-logo {{
            width: 125px !important;
            height: 125px !important;
            min-width: 125px !important;
            max-width: 125px !important;
            object-fit: contain;
            padding: 7px;
            border-radius: 18px;
            background: rgba(255, 255, 255, 0.08);
            box-shadow: 0 7px 20px rgba(0, 0, 0, 0.25);
        }}

        /* Franja de novedades. */
        .novedades-strip {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            min-height: 44px;
            margin: 0.3rem 0 1.2rem 0;
            padding: 0.7rem 1rem;
            border: 1px solid rgba(170, 141, 175, 0.20);
            border-radius: 14px;
            background: {COLOR_PANEL};
        }}

        .panel-title {{
            margin: 0 0 0.25rem 0;
            color: {COLOR_BLANCO};
            font-size: 1.15rem;
            font-weight: 800;
        }}

        .panel-caption {{
            margin-bottom: 0.8rem;
            color: {COLOR_TEXTO_SECUNDARIO};
            font-size: 0.92rem;
        }}

        /* Indicadores de la columna derecha. */
        .metric-card {{
            min-height: 104px;
            margin-bottom: 0.75rem;
            padding: 1rem 1.1rem;
            border: 1px solid rgba(170, 141, 175, 0.24);
            border-left: 7px solid {COLOR_LILA};
            border-radius: 16px;
            background: {COLOR_PANEL};
            box-shadow: 0 6px 18px rgba(0, 0, 0, 0.28);
        }}

        .metric-title {{
            color: {COLOR_TEXTO_SECUNDARIO};
            font-size: 0.85rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }}

        .metric-value {{
            margin-top: 0.18rem;
            color: {COLOR_BLANCO};
            font-size: 1.75rem;
            line-height: 1.15;
            font-weight: 850;
        }}

        .metric-subtitle {{
            min-height: 1.15rem;
            margin-top: 0.22rem;
            color: {COLOR_TEXTO_SECUNDARIO};
            font-size: 0.78rem;
        }}

        /*
        Advertencia sobre ediciones y acabados.

        El fondo es rojo oscuro y el mensaje utiliza rojo brillante para
        llamar la atención sin confundirse con los indicadores normales.
        */
        /*
        Orden responsive de los indicadores.

        En computador se usa la tarjeta ubicada en la columna derecha.
        En teléfono se muestra una copia inmediatamente después de novedades,
        antes de los dos buscadores.
        */
        .mobile-only {{
            display: none !important;
        }}

        .desktop-only {{
            display: block !important;
        }}

        .mobile-ck-wrapper {{
            margin: 0.2rem 0 1rem 0;
        }}

        .st-key-mobile_info_row {{
            width: 100% !important;
        }}


        .section-title {{
            margin-top: 1.8rem;
            color: {COLOR_BLANCO};
            font-size: 1.35rem;
            font-weight: 850;
        }}

        /* Título principal de la zona de resultados. */
        .results-title {{
            margin-bottom: 0.35rem;
            font-size: 1.72rem;
            line-height: 1.15;
        }}

        /*
        Valores de precio de las tarjetas.
        La etiqueta permanece blanca y el monto se destaca en verde.
        */
        .card-price-line {{
            margin: 0.2rem 0;
            color: {COLOR_TEXTO};
            font-weight: 700;
        }}

        .card-price-value {{
            color: #35D07F;
            font-size: 1.05rem;
            font-weight: 850;
        }}

        /* Entradas de texto para la búsqueda individual y la lista. */
        div[data-testid="stTextInput"] input,
        div[data-testid="stTextArea"] textarea {{
            border: 1.5px solid {COLOR_GRIS_VIOLETA};
            border-radius: 14px;
            background: {COLOR_PANEL};
            color: {COLOR_BLANCO};
        }}

        div[data-testid="stTextInput"] input:focus,
        div[data-testid="stTextArea"] textarea:focus {{
            border-color: #A95AFF;
            box-shadow: 0 0 0 2px rgba(169, 90, 255, 0.20);
        }}

        .empty-warning {{
            margin-top: 1rem;
            padding: 0.9rem 1rem;
            border: 1px solid #8F62B0;
            border-radius: 12px;
            background: #2B2135;
            color: #F7F4FA;
            font-weight: 650;
        }}

        /* Navegación principal */
        .main-nav-label {{
            margin: 0.2rem 0 0.45rem 0;
            color: {COLOR_TEXTO_SECUNDARIO};
            font-size: 0.78rem;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }}

        [class*="st-key-vista_principal_"] .stButton > button {{
            min-height: 50px !important;
            border-radius: 12px !important;
            font-size: 0.92rem !important;
            font-weight: 900 !important;
        }}

        [class*="st-key-vista_principal_"] .stButton > button[kind="primary"] {{
            background: linear-gradient(135deg, #6F31A8 0%, #842BD1 100%) !important;
            border-color: #B66CFF !important;
        }}

        /* Todos los botones usan la misma paleta del boceto. */
        .stButton > button,
        .stDownloadButton > button,
        .stLinkButton > a {{
            min-height: 44px;
            border: 1px solid rgba(170, 141, 175, 0.35);
            border-radius: 12px;
            font-weight: 800;
        }}

        .stButton > button[kind="primary"],
        .stDownloadButton > button,
        .stLinkButton > a {{
            background: #842BD1;
            color: {COLOR_BLANCO};
        }}

        .stButton > button:hover,
        .stDownloadButton > button:hover,
        .stLinkButton > a:hover {{
            border-color: #B66CFF;
            background: #9B3BE5;
            color: {COLOR_BLANCO};
        }}

        /* Tarjetas de cada impresión encontrada. */
        div[data-testid="stVerticalBlockBorderWrapper"] {{
            border-color: rgba(170, 141, 175, 0.22);
        }}

        /*
        Pestañas finas de "Vista de cartas" y "Carro".
        Mantienen el aspecto compacto que tenía la interfaz anterior,
        especialmente útil en pantallas de celular.
        */
        /*
        Tamaño de las pestañas de resultados.

        Streamlit coloca el texto dentro de elementos <p> y <span>, por lo
        que no basta con cambiar solamente el tamaño del botón. Estas reglas
        también modifican explícitamente sus elementos internos.
        */
        button[data-baseweb="tab"] {{
            min-height: 56px;
            padding: 0.65rem 1.25rem;
            background: transparent !important;
            border-radius: 0 !important;
            color: {COLOR_TEXTO_SECUNDARIO};
            font-weight: 850;
        }}

        button[data-baseweb="tab"] p,
        button[data-baseweb="tab"] span,
        button[data-baseweb="tab"]
        div[data-testid="stMarkdownContainer"] p {{
            margin: 0 !important;
            font-size: 1.28rem !important;
            line-height: 1.2 !important;
            font-weight: 850 !important;
        }}

        button[data-baseweb="tab"][aria-selected="true"] {{
            color: #D4A7FF !important;
        }}

        div[data-baseweb="tab-highlight"] {{
            background-color: #842BD1 !important;
            height: 3px;
        }}

        /*
        TABLA FIJA Y RESPONSIVE DEL CARRO

        El encabezado y cada fila usan contenedores con claves diferentes.
        Esto permite dar una altura fina al encabezado y una altura suficiente
        a las cartas, sin cortar texto, botones ni imágenes.
        */
        .st-key-carro_tabla_manual {{
            width: 100%;
            overflow-x: auto !important;
            overflow-y: hidden !important;
            border: 1px solid rgba(170, 141, 175, 0.30);
            border-radius: 12px;
            background: {COLOR_FONDO};
        }}

        /*
        Elimina el espacio vertical automático que Streamlit coloca entre
        contenedores. Las filas quedan separadas únicamente por una línea fina.
        */
        .st-key-carro_tabla_manual
        > div
        > [data-testid="stVerticalBlock"],
        .st-key-carro_tabla_manual
        [data-testid="stVerticalBlockBorderWrapper"]
        > div
        > [data-testid="stVerticalBlock"] {{
            gap: 0 !important;
            row-gap: 0 !important;
        }}

        .st-key-carro_tabla_encabezado,
        [class*="st-key-carro_fila_"] {{
            margin: 0 !important;
            padding: 0 !important;
        }}

        /*
        Cuadrícula compartida:
        Carta | Valor | − | Cantidad | + | Edición | Acabado | Imagen
        */
        .st-key-carro_tabla_encabezado
        [data-testid="stHorizontalBlock"],
        [class*="st-key-carro_fila_"]
        [data-testid="stHorizontalBlock"] {{
            display: grid !important;
            grid-template-columns:
                minmax(165px, 1.15fr)
                105px
                42px
                82px
                42px
                minmax(180px, 1.15fr)
                105px
                84px !important;
            width: 100% !important;
            min-width: 850px !important;
            gap: 0 !important;
            align-items: stretch !important;
        }}

        /*
        Encabezado fino y centrado.
        */
        .st-key-carro_tabla_encabezado {{
            background: #1B1A23;
            border-bottom: 1px solid rgba(170, 141, 175, 0.24);
        }}

        .st-key-carro_tabla_encabezado
        [data-testid="stHorizontalBlock"] {{
            min-height: 34px !important;
            height: 34px !important;
        }}

        /*
        Filas de productos. Se usa altura mínima y no una altura rígida:
        si un nombre o edición necesita dos líneas, la fila puede crecer.
        */
        [class*="st-key-carro_fila_"] {{
            background: #15141B;
            border-bottom: 1px solid rgba(170, 141, 175, 0.24);
        }}

        [class*="st-key-carro_fila_"]:last-child {{
            border-bottom: none;
        }}

        [class*="st-key-carro_fila_"]
        [data-testid="stHorizontalBlock"] {{
            min-height: 82px !important;
            height: auto !important;
        }}

        /*
        Streamlit ha usado ambos selectores según la versión.
        */
        .st-key-carro_tabla_encabezado
        [data-testid="stColumn"],
        .st-key-carro_tabla_encabezado
        [data-testid="column"],
        [class*="st-key-carro_fila_"]
        [data-testid="stColumn"],
        [class*="st-key-carro_fila_"]
        [data-testid="column"] {{
            width: auto !important;
            min-width: 0 !important;
            max-width: none !important;
            flex: none !important;
            padding: 0.45rem !important;
            border-right: 1px solid rgba(170, 141, 175, 0.20);
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
        }}

        .st-key-carro_tabla_encabezado
        [data-testid="stColumn"] {{
            padding-top: 0 !important;
            padding-bottom: 0 !important;
        }}

        .st-key-carro_tabla_encabezado
        [data-testid="stColumn"]:last-child,
        .st-key-carro_tabla_encabezado
        [data-testid="column"]:last-child,
        [class*="st-key-carro_fila_"]
        [data-testid="stColumn"]:last-child,
        [class*="st-key-carro_fila_"]
        [data-testid="column"]:last-child {{
            border-right: none;
        }}

        .cart-table-header {{
            display: flex;
            width: 100%;
            height: 100%;
            align-items: center;
            justify-content: center;
            color: {COLOR_TEXTO_SECUNDARIO};
            font-size: 0.82rem;
            line-height: 1;
            font-weight: 850;
            text-transform: uppercase;
            letter-spacing: 0.025em;
            text-align: center;
            white-space: nowrap;
        }}

        .cart-table-cell {{
            display: flex;
            width: 100%;
            min-height: 66px;
            align-items: center;
            justify-content: center;
            color: {COLOR_TEXTO};
            font-size: 0.92rem;
            line-height: 1.25;
            text-align: center;
            overflow-wrap: anywhere;
        }}

        .cart-table-card {{
            font-weight: 800;
        }}

        .cart-table-value {{
            color: {COLOR_BLANCO};
            font-weight: 850;
            white-space: nowrap;
        }}

        .cart-table-quantity {{
            font-weight: 900;
        }}

        /*
        Botones compactos y centrados.
        */
        [class*="st-key-carro_fila_"] .stButton {{
            display: flex;
            width: 100%;
            min-height: 66px;
            align-items: center;
            justify-content: center;
        }}

        [class*="st-key-carro_fila_"] .stButton > button {{
            width: 32px !important;
            min-width: 32px !important;
            max-width: 32px !important;
            min-height: 32px !important;
            height: 32px !important;
            padding: 0 !important;
            border-radius: 8px !important;
            font-size: 1rem !important;
            line-height: 1 !important;
        }}

        [class*="st-key-carro_fila_"] img {{
            display: block;
            width: 50px !important;
            max-width: 50px !important;
            max-height: 70px !important;
            margin: 0 auto;
            border-radius: 4px;
            object-fit: contain;
        }}

        .st-key-carro_tabla_manual::-webkit-scrollbar {{
            height: 7px;
        }}

        .st-key-carro_tabla_manual::-webkit-scrollbar-track {{
            background: {COLOR_PANEL};
        }}

        .st-key-carro_tabla_manual::-webkit-scrollbar-thumb {{
            background: {COLOR_MORADO_MEDIO};
            border-radius: 999px;
        }}

        .cart-sort-caption {{
            margin-top: 0.15rem;
            color: {COLOR_TEXTO_SECUNDARIO};
            font-size: 0.78rem;
        }}

        .cart-mobile-hint {{
            display: none;
            margin: 0.45rem 0 0.65rem 0;
            color: #D4A7FF;
            font-size: 0.82rem;
            font-weight: 750;
            text-align: center;
        }}

        /*
        En teléfono se compactan las primeras columnas para priorizar:
        Carta | Valor | − | Cantidad | +
        */
        @media (max-width: 900px) {{
            .cart-mobile-hint {{
                display: block;
            }}

            .st-key-carro_tabla_encabezado
            [data-testid="stHorizontalBlock"],
            [class*="st-key-carro_fila_"]
            [data-testid="stHorizontalBlock"] {{
                grid-template-columns:
                    130px
                    82px
                    34px
                    58px
                    34px
                    170px
                    92px
                    76px !important;
                width: 676px !important;
                min-width: 676px !important;
            }}

            .st-key-carro_tabla_encabezado
            [data-testid="stHorizontalBlock"] {{
                min-height: 31px !important;
                height: 31px !important;
            }}

            [class*="st-key-carro_fila_"]
            [data-testid="stHorizontalBlock"] {{
                min-height: 78px !important;
                height: auto !important;
            }}

            .st-key-carro_tabla_encabezado
            [data-testid="stColumn"],
            .st-key-carro_tabla_encabezado
            [data-testid="column"],
            [class*="st-key-carro_fila_"]
            [data-testid="stColumn"],
            [class*="st-key-carro_fila_"]
            [data-testid="column"] {{
                padding: 0.35rem 0.25rem !important;
            }}

            .cart-table-header {{
                font-size: 0.72rem;
            }}

            .cart-table-cell {{
                min-height: 62px;
                font-size: 0.82rem;
            }}

            .cart-table-card {{
                font-size: 0.84rem;
            }}

            [class*="st-key-carro_fila_"] .stButton {{
                min-height: 62px;
            }}

            [class*="st-key-carro_fila_"] .stButton > button {{
                width: 29px !important;
                min-width: 29px !important;
                max-width: 29px !important;
                min-height: 29px !important;
                height: 29px !important;
            }}

            [class*="st-key-carro_fila_"] img {{
                width: 45px !important;
                max-width: 45px !important;
                max-height: 64px !important;
            }}
        }}

        .purchase-note {{
            min-height: 92px;
            padding: 1rem 1.1rem;
            border: 1px solid #81C784;
            border-radius: 14px;
            background: #A5D6A7;
            color: #17351C;
            box-shadow: 0 6px 18px rgba(67, 160, 71, 0.12);
        }}

        .purchase-note strong {{
            color: #17351C;
        }}

        /*
        Botón específico de WhatsApp.

        El selector usa la clave del contenedor para no modificar los demás
        botones verdes, violetas o enlaces de la aplicación.
        */
        .st-key-whatsapp_compra .stLinkButton > a,
        .st-key-whatsapp_compra_mobile .stLinkButton > a {{
            min-height: 58px;
            border: 1px solid #43A047 !important;
            border-radius: 14px !important;
            background: #43A047 !important;
            color: #FFFFFF !important;
            font-weight: 850 !important;
            box-shadow: 0 6px 18px rgba(67, 160, 71, 0.22);
        }}

        .st-key-whatsapp_compra .stLinkButton > a:hover,
        .st-key-whatsapp_compra_mobile .stLinkButton > a:hover {{
            border-color: #388E3C !important;
            background: #388E3C !important;
            color: #FFFFFF !important;
        }}

        /* En computador se usa el botón alineado junto a Indicaciones. */
        .st-key-whatsapp_compra_mobile {{
            display: none;
        }}

        .st-key-acciones_compra_mobile {{
            display: none;
        }}

        .st-key-acciones_compra_desktop {{
            display: block;
        }}

        .st-key-acciones_compra_mobile [data-testid="stHorizontalBlock"] {{
            gap: 0.5rem !important;
        }}


        /* Navegación principal en una sola fila. */
        .st-key-vista_principal_navigation {{
            width: 100% !important;
        }}

        .st-key-vista_principal_navigation
        [data-testid="stHorizontalBlock"] {{
            width: 100% !important;
            gap: 0.65rem !important;
        }}

        .st-key-vista_principal_navigation
        [data-testid="stColumn"] {{
            min-width: 0 !important;
        }}

        .st-key-vista_principal_navigation .stButton,
        .st-key-vista_principal_navigation .stButton > button {{
            width: 100% !important;
        }}

        @media (max-width: 900px) {{

            /* Mantener las cuatro pestañas en una fila
               horizontalmente en teléfono. */
            .st-key-vista_principal_navigation
            [data-testid="stHorizontalBlock"] {{
                display: grid !important;
                grid-template-columns: repeat(4, minmax(0, 1fr)) !important;
                width: 100% !important;
                gap: 0.45rem !important;
            }}

            .st-key-vista_principal_navigation
            [data-testid="stColumn"] {{
                width: auto !important;
                min-width: 0 !important;
                max-width: none !important;
                flex: none !important;
            }}

            .st-key-vista_principal_navigation .stButton > button {{
                min-height: 52px !important;
                padding: 0.4rem 0.2rem !important;
                font-size: 0.78rem !important;
                line-height: 1.05 !important;
                white-space: normal !important;
            }}

            [class*="st-key-vista_principal_"] .stButton > button {{
                min-height: 44px !important;
                padding: 0.45rem 0.25rem !important;
                font-size: 0.76rem !important;
            }}

            .st-key-mobile_info_row .metric-title {{
                font-size: 0.72rem !important;
                letter-spacing: 0.04em !important;
            }}

            .st-key-mobile_info_row .metric-value {{
                font-size: 1.02rem !important;
                line-height: 1.02 !important;
                white-space: nowrap !important;
                margin-top: 0.12rem !important;
            }}

            .st-key-mobile_info_row .metric-subtitle {{
                font-size: 0.58rem !important;
                line-height: 1.08 !important;
                margin-top: 0.12rem !important;
            }}

            .buscador-hero {{
                min-height: 130px;
                padding: 1.2rem;
                gap: 1rem;
            }}

            .buscador-hero h1 {{
                font-size: 2rem;
            }}

            .hero-logo {{
                width: 85px !important;
                height: 85px !important;
                min-width: 85px !important;
                max-width: 85px !important;
            }}

            .novedades-strip {{
                align-items: flex-start;
                flex-direction: column;
            }}

            /*
            En teléfono el botón de WhatsApp aparece dentro del bloque de
            acciones, justo debajo de "Vaciar carro". La copia de escritorio
            se oculta para que no se duplique.
            */
            .st-key-acciones_compra_desktop {{
                display: none !important;
            }}

            .st-key-acciones_compra_mobile {{
                display: block !important;
                margin-top: 0.2rem;
            }}

            .st-key-acciones_compra_mobile [data-testid="stHorizontalBlock"] {{
                display: grid !important;
                grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
                gap: 0.5rem !important;
            }}

            .st-key-acciones_compra_mobile [data-testid="stColumn"] {{
                width: auto !important;
                min-width: 0 !important;
                max-width: none !important;
                flex: none !important;
            }}

            .st-key-acciones_compra_mobile .stButton > button,
            .st-key-acciones_compra_mobile .stDownloadButton > button {{
                min-height: 52px !important;
                padding: 0.45rem 0.25rem !important;
                font-size: 0.78rem !important;
            }}

            .st-key-whatsapp_compra_mobile {{
                display: block;
                margin-top: 0.5rem;
            }}

            .st-key-whatsapp_compra {{
                display: none;
            }}

            /*
            En pantallas pequeñas se invierte qué copia del valor CK aparece.
            El resto del resumen, como Sugerencias o Cartas encontradas,
            permanece después de los buscadores.
            */
            .mobile-only {{
                display: block !important;
            }}

            .desktop-only {{
                display: none !important;
            }}

            .mobile-ck-wrapper {{
                margin-bottom: 1rem;
            }}

            button[data-baseweb="tab"] {{
                min-height: 50px;
                padding: 0.55rem 0.9rem;
            }}

            button[data-baseweb="tab"] p,
            button[data-baseweb="tab"] span,
            button[data-baseweb="tab"]
            div[data-testid="stMarkdownContainer"] p {{
                font-size: 1.12rem !important;
            }}

            .results-title {{
                font-size: 1.48rem;
            }}

        }}
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# ENCABEZADO
# ============================================================

logo_html = ""
if LOGO_PATH.is_file():
    logo_base64 = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
    logo_html = (
        f'<img class="hero-logo" src="data:image/png;base64,{logo_base64}" '
        'alt="Logo de Tienda de Magic UC">'
    )

st.markdown(
    f"""
    <section class="buscador-hero">
        <h1>Tienda de Magic UC</h1>
        {logo_html}
    </section>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="novedades-strip">¡Revisa las novedades y el stock de la tienda Magic UC!</div>
    """,
    unsafe_allow_html=True,
)

with st.container(key="mobile_info_row"):
    tarjeta_metrica(
        "Valor CK tienda",
        f"1 CK = ${VALOR_CK_CLP} CLP",
        "Conversión fija utilizada por la tienda",
        clase_extra="mobile-only mobile-ck-wrapper",
    )


def archivo_y_version_inventario() -> tuple[Path, tuple[str, int, int] | None]:
    for archivo in (ARCHIVO_CSV, ARCHIVO_EXCEL):
        try:
            datos = archivo.stat()
        except FileNotFoundError:
            continue
        return archivo, (archivo.name, datos.st_mtime_ns, datos.st_size)
    return ARCHIVO_CSV, None


@st.fragment(run_every="30s")
def revisar_cambios_inventario() -> None:
    _, version = archivo_y_version_inventario()
    if version != st.session_state["version_inventario"]:
        st.rerun(scope="app")


archivo_inventario, version = archivo_y_version_inventario()
if st.session_state.get("version_inventario") != version:
    for clave in (
        "resultados",
        "no_encontradas",
        "total_buscado",
        "total_encontrado",
        "tipo_busqueda_realizada",
    ):
        st.session_state.pop(clave, None)
st.session_state["version_inventario"] = version

try:
    inventario = cargar_inventario_compartido(
        archivo_inventario.read_bytes(),
        archivo_inventario.suffix,
    )
except FileNotFoundError:
    inventario = []
    st.info("El catálogo estará disponible cuando la tienda publique su inventario.")
except Exception as error:
    inventario = []
    st.error(f"No se pudo leer el archivo de inventario: {error}")
else:
    if inventario:
        inventario = completar_inventario(inventario)
    else:
        st.info("Por ahora no hay cartas en stock.")

revisar_cambios_inventario()


def render_tarjetas(resultados: list[dict[str, Any]], contexto: str) -> None:
    for inicio_fila in range(0, len(resultados), 3):
        columnas = st.columns(3)

        for desplazamiento, (columna, fila) in enumerate(
            zip(
                columnas,
                resultados[inicio_fila:inicio_fila + 3],
            )
        ):
            indice_resultado = inicio_fila + desplazamiento

            with columna:
                with st.container(border=True):
                    if fila.get("Imagen"):
                        st.image(
                            fila["Imagen"],
                            use_container_width=True,
                        )
                    else:
                        st.info("Imagen no disponible")

                    st.markdown(f"### {fila['Carta']}")

                    if fila.get("Tipo"):
                        st.caption(fila["Tipo"])

                    st.markdown(
                        (
                            '<div class="card-price-line">'
                            'Precio CK USD: '
                            '<span class="card-price-value">'
                            f"{precio_usd_texto(fila['Precio CK'])}"
                            "</span></div>"
                        ),
                        unsafe_allow_html=True,
                    )

                    st.markdown(
                        (
                            '<div class="card-price-line">'
                            'Precio CLP: '
                            '<span class="card-price-value">'
                            f"{precio_clp_texto(fila['Precio CLP'])}"
                            "</span></div>"
                        ),
                        unsafe_allow_html=True,
                    )

                    st.write(
                        "**Cantidad disponible:** "
                        f"{fila['Cantidad disponible']}"
                    )
                    for campo in ("Edición", "Acabado", "Idioma"):
                        if fila.get(campo):
                            st.write(f"**{campo}:** {fila[campo]}")

                    if st.button(
                        "Añadir al carro",
                        key=(
                            f"agregar_{contexto}_{fila['ID item']}_"
                            f"{indice_resultado}"
                        ),
                        type="primary",
                        use_container_width=True,
                    ):
                        agregar_al_carro(fila)
                        st.rerun()


COLORES_CATALOGO = {
    "W": "Blanco", "U": "Azul", "B": "Negro", "R": "Rojo", "G": "Verde",
}
ACABADOS_CATALOGO = {
    "nonfoil": "No foil", "foil": "Foil", "etched": "Etched", "": "Sin especificar",
}


def color_catalogo(carta: dict[str, Any]) -> str:
    colores = carta.get("colores")
    if colores is None:
        return "Sin datos de color"
    if len(colores) > 1:
        return "Multicolor"
    if not colores:
        return "Incoloro"
    return COLORES_CATALOGO[colores[0]]


def render_catalogo(inventario: list[dict[str, Any]]) -> None:
    if not inventario:
        return

    colores = ["Todos", *COLORES_CATALOGO.values(), "Multicolor", "Incoloro"]
    acabados = sorted({carta["finish"] for carta in inventario}, key=lambda f: ACABADOS_CATALOGO.get(f, f))
    columna_color, columna_acabado = st.columns(2)
    with columna_color:
        color = st.selectbox("Color", colores, key="catalogo_color")
    with columna_acabado:
        acabado = st.selectbox(
            "Acabado",
            [None, *acabados],
            format_func=lambda f: "Todos" if f is None else ACABADOS_CATALOGO.get(f, f),
            key="catalogo_acabado",
        )

    filtradas = sorted(
        (
            carta for carta in inventario
            if (color == "Todos" or color_catalogo(carta) == color)
            and (acabado is None or carta["finish"] == acabado)
        ),
        key=lambda carta: (carta["nombre"].casefold(), carta["set_name"], carta["finish"]),
    )
    if not filtradas:
        st.info("No hay cartas que coincidan con estos filtros.")
        return

    total_copias = sum(carta["cantidad"] for carta in filtradas)
    st.caption(f"Entradas: {len(filtradas)} · Copias disponibles: {total_copias}")
    por_pagina = 24
    total_paginas = (len(filtradas) + por_pagina - 1) // por_pagina
    pagina = 1
    if total_paginas > 1:
        pagina = st.selectbox("Página", range(1, total_paginas + 1), key="catalogo_pagina")
    inicio = (pagina - 1) * por_pagina
    render_tarjetas(
        [preparar_resultado(carta) for carta in filtradas[inicio:inicio + por_pagina]],
        "catalogo",
    )


def render_carrito_completo() -> None:
    """Renderiza la vista completa del carrito."""
    carro = st.session_state.setdefault("carro", {})

    if not carro:
        st.info(
            "El carro está vacío. Añade cartas en la sección Vista de cartas."
        )
    else:
        # --------------------------------------------------------
        # TABLA FIJA DEL CARRO
        # --------------------------------------------------------
        #
        # No se utiliza st.dataframe para impedir que los visitantes
        # oculten, permuten, fijen o redimensionen las columnas.
        #
        # En móvil se priorizan:
        # Carta | Valor | − | Cantidad | +
        #
        # Edición, Acabado e Imagen quedan disponibles mediante
        # desplazamiento horizontal.
        columna_orden, columna_direccion = st.columns(
            [1.35, 1],
            gap="small",
        )

        with columna_orden:
            ordenar_por = st.selectbox(
                "Ordenar carro por",
                [
                    "Carta",
                    "Valor",
                    "Cantidad",
                    "Edición",
                    "Acabado",
                ],
                key="ordenar_carro_por",
            )

        with columna_direccion:
            direccion_orden = st.radio(
                "Dirección",
                ["Ascendente", "Descendente"],
                horizontal=True,
                key="direccion_orden_carro",
            )

        st.markdown(
            (
                '<div class="cart-sort-caption">'
                "La estructura de la tabla es fija; solo puede "
                "cambiarse el orden de las filas."
                "</div>"
            ),
            unsafe_allow_html=True,
        )

        def clave_orden_carro(
            elemento: tuple[str, dict[str, Any]],
        ) -> Any:
            _, item = elemento
            cantidad = int(item.get("Cantidad", 1))
            precio_unitario = item.get("Precio CLP")
            subtotal = (
                round(float(precio_unitario) * cantidad)
                if precio_unitario is not None
                else -1
            )

            valores = {
                "Carta": str(item.get("Carta", "")).casefold(),
                "Valor": subtotal,
                "Cantidad": cantidad,
                "Edición": str(
                    item.get("Edición", "")
                ).casefold(),
                "Acabado": str(
                    item.get("Acabado", "")
                ).casefold(),
            }

            return valores[ordenar_por]

        items_ordenados = sorted(
            carro.items(),
            key=clave_orden_carro,
            reverse=direccion_orden == "Descendente",
        )

        # Proporciones de escritorio:
        # Carta | Valor | − | Cantidad | + | Edición | Acabado | Imagen
        #
        # El CSS conserva una versión compacta de 620 px en teléfonos.
        proporciones_tabla = [1, 1, 1, 1, 1, 1, 1, 1]

        st.markdown(
            (
                '<div class="cart-mobile-hint">'
                "Desliza la tabla para más detalle →"
                "</div>"
            ),
            unsafe_allow_html=True,
        )

        with st.container(key="carro_tabla_manual"):
            # El encabezado tiene una clave propia para que solo esta fila
            # reciba la altura fina de 34 px.
            with st.container(key="carro_tabla_encabezado"):
                columnas_encabezado = st.columns(
                    proporciones_tabla,
                    gap="small",
                    vertical_alignment="center",
                )

                encabezados = [
                    "Carta",
                    "Valor",
                    "",
                    "Cantidad",
                    "",
                    "Edición",
                    "Acabado",
                    "Imagen",
                ]

                for columna, encabezado in zip(
                    columnas_encabezado,
                    encabezados,
                ):
                    with columna:
                        st.markdown(
                            (
                                '<div class="cart-table-header">'
                                f"{encabezado}"
                                "</div>"
                            ),
                            unsafe_allow_html=True,
                        )

            for numero_fila, (item_id, item) in enumerate(
                items_ordenados
            ):
                cantidad = int(item.get("Cantidad", 1))
                stock_maximo = max(
                    0,
                    int(item.get("Cantidad disponible") or 0),
                )
                precio_unitario = item.get("Precio CLP")
                subtotal = (
                    round(float(precio_unitario) * cantidad)
                    if precio_unitario is not None
                    else None
                )

                # Cada producto usa una clave propia. Así el CSS puede
                # distinguirlo del encabezado y no corta su contenido.
                with st.container(
                    key=f"carro_fila_{numero_fila}_{item_id}"
                ):
                    columnas_fila = st.columns(
                        proporciones_tabla,
                        gap="small",
                        vertical_alignment="center",
                    )

                    with columnas_fila[0]:
                        st.markdown(
                            (
                                '<div class="cart-table-cell '
                                'cart-table-card">'
                                f"{item.get('Carta', '')}"
                                "</div>"
                            ),
                            unsafe_allow_html=True,
                        )

                    with columnas_fila[1]:
                        st.markdown(
                            (
                                '<div class="cart-table-cell '
                                'cart-table-value">'
                                f"{precio_clp_texto(subtotal)}"
                                "</div>"
                            ),
                            unsafe_allow_html=True,
                        )

                    with columnas_fila[2]:
                        if st.button(
                            "−",
                            key=f"carro_restar_{item_id}",
                            help="Disminuir una unidad",
                        ):
                            ajustar_cantidad_carro(item_id, -1)
                            st.rerun()

                    with columnas_fila[3]:
                        st.markdown(
                            (
                                '<div class="cart-table-cell '
                                'cart-table-quantity">'
                                f"{cantidad}"
                                "</div>"
                            ),
                            unsafe_allow_html=True,
                        )

                    with columnas_fila[4]:
                        if st.button(
                            "+",
                            key=f"carro_sumar_{item_id}",
                            help="Añadir una unidad",
                            disabled=cantidad >= stock_maximo,
                        ):
                            ajustar_cantidad_carro(item_id, 1)
                            st.rerun()

                    with columnas_fila[5]:
                        st.markdown(
                            (
                                '<div class="cart-table-cell">'
                                f"{item.get('Edición', '')}"
                                "</div>"
                            ),
                            unsafe_allow_html=True,
                        )

                    with columnas_fila[6]:
                        st.markdown(
                            (
                                '<div class="cart-table-cell">'
                                f"{item.get('Acabado', '')}"
                                "</div>"
                            ),
                            unsafe_allow_html=True,
                        )

                    with columnas_fila[7]:
                        if item.get("Imagen"):
                            st.image(
                                item["Imagen"],
                                width=50,
                            )

        compra_df = dataframe_compra()
        total_compra = (
            float(
                compra_df["Subtotal CLP"]
                .fillna(0)
                .sum()
            )
            if not compra_df.empty
            else 0.0
        )

        csv_compra = compra_df.to_csv(
            index=False,
            sep=";",
            decimal=",",
        ).encode("utf-8-sig")

        # --------------------------------------------------------
        # RESUMEN FINAL RESPONSIVE
        # --------------------------------------------------------
        #
        # En computador:
        #   [ Valor total ] [ Descargar compra ]
        #                   [ Vaciar carro     ]
        #
        # En teléfono, Streamlit apila automáticamente las columnas:
        #   Valor total
        #   Descargar compra
        #   Vaciar carro
        #
        # De esta forma cambia la distribución de escritorio sin alterar
        # el orden vertical que ya funciona bien en celulares.
        columna_total, columna_acciones = st.columns(
            [1.15, 1],
            gap="large",
            vertical_alignment="top",
        )

        with columna_total:
            tarjeta_metrica(
                "Valor total",
                precio_clp_texto(total_compra),
                "Suma de los subtotales del carro",
            )

        with columna_acciones:
            # Escritorio: botones apilados como antes.
            with st.container(key="acciones_compra_desktop"):
                st.download_button(
                    "Descargar compra (.csv)",
                    data=csv_compra,
                    file_name="Compra_Tienda_de_Magic_UC.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

                if st.button(
                    "Vaciar carro",
                    key="vaciar_carro_compra",
                    use_container_width=True,
                ):
                    st.session_state["carro"] = {}
                    st.session_state["carro_editor_version"] = (
                        st.session_state.get(
                            "carro_editor_version",
                            0,
                        ) + 1
                    )
                    st.rerun()

            # Móvil: ambos controles ocupan una sola fila.
            with st.container(key="acciones_compra_mobile"):
                accion_descargar, accion_vaciar = st.columns(
                    [1, 1],
                    gap="small",
                )

                with accion_descargar:
                    st.download_button(
                        "Descargar CSV",
                        data=csv_compra,
                        file_name="Compra_Tienda_de_Magic_UC.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )

                with accion_vaciar:
                    if st.button(
                        "Vaciar",
                        key="vaciar_carro_compra_mobile",
                        use_container_width=True,
                    ):
                        st.session_state["carro"] = {}
                        st.session_state["carro_editor_version"] = (
                            st.session_state.get(
                                "carro_editor_version",
                                0,
                            ) + 1
                        )
                        st.rerun()

            with st.container(key="whatsapp_compra_mobile"):
                st.link_button(
                    "Contactar por WhatsApp",
                    whatsapp_url_carrito(
                        st.session_state.get("carro", {})
                    ),
                    use_container_width=True,
                )

        # --------------------------------------------------------
        # INDICACIONES Y CONTACTO
        # --------------------------------------------------------
        #
        # En computador aparecen en una misma fila:
        # [ Indicaciones de compra ] [ Contactar por WhatsApp ]
        #
        # En celular Streamlit apila ambas columnas y conserva este orden.
        # Usa exactamente la misma proporción que el bloque superior
        # de Valor total / acciones. Así ambos lados comienzan y terminan
        # en las mismas posiciones horizontales.
        columna_indicaciones, columna_whatsapp = st.columns(
            [1.15, 1],
            gap="large",
            vertical_alignment="center",
        )

        with columna_indicaciones:
            st.markdown(
                """
                <div class="purchase-note">
                    <strong>INDICACIONES DE COMPRA</strong><br>
                    Descarga el archivo de compra y adjúntalo en WhatsApp
                    para confirmar disponibilidad, forma de pago y entrega.
                </div>
                """,
                unsafe_allow_html=True,
            )

        with columna_whatsapp:
            with st.container(key="whatsapp_compra"):
                st.link_button(
                    "Contactar por WhatsApp",
                    whatsapp_url_carrito(
                        st.session_state.get("carro", {})
                    ),
                    use_container_width=True,
                )



# ============================================================
# NAVEGACIÓN PRINCIPAL Y TRES VISTAS
# ============================================================

st.markdown(
    '<div class="main-nav-label">Navegación</div>',
    unsafe_allow_html=True,
)

vista_principal = selector_botones(
    "vista_principal",
    ["Catálogo", "Buscar cartas", "Lista de cartas", "Carrito"],
    "Catálogo",
)

# ============================================================
# VISTA 1: BUSCAR CARTAS
# ============================================================

resultados_previsualizacion: list[dict[str, Any]] = []
previsualizacion_activa = False

if vista_principal == "Catálogo":
    render_catalogo(inventario)

elif vista_principal == "Buscar cartas":
    columna_buscador, columna_resumen = st.columns(
        [1.45, 1],
        gap="large",
    )

    with columna_buscador:
        if st_searchbox is None:
            st.error(
                "Falta instalar `streamlit-searchbox`. Agrega "
                "`streamlit-searchbox==0.1.24` a requirements.txt."
            )
            valor_buscador: Any = st.text_input(
                "Nombre de la carta",
                placeholder="Escribe al menos 3 caracteres",
                label_visibility="collapsed",
            )
        else:
            estilo_buscador = {
                "clear": {
                    "clearable": "always",
                    "icon": "cross",
                    "width": 18,
                    "height": 18,
                },
                "searchbox": {
                    "control": {
                        "backgroundColor": COLOR_PANEL,
                        "borderColor": "#A95AFF",
                        "borderRadius": "12px",
                        "minHeight": "46px",
                        "boxShadow": "none",
                    },
                    "menu": {
                        "backgroundColor": COLOR_PANEL,
                        "border": "1px solid rgba(170, 141, 175, 0.35)",
                        "borderRadius": "10px",
                        "overflow": "hidden",
                        "zIndex": 9999,
                    },
                    "menuList": {
                        "backgroundColor": COLOR_PANEL,
                        "maxHeight": "260px",
                        "padding": 0,
                    },
                    "option": {
                        "color": COLOR_TEXTO,
                        "backgroundColor": COLOR_PANEL,
                        "padding": "8px 12px",
                        "cursor": "pointer",
                        "highlightColor": "#D4A7FF",
                    },
                    "input": {
                        "color": COLOR_BLANCO,
                    },
                    "placeholder": {
                        "color": "#AFA8BA",
                    },
                    "singleValue": {
                        "color": COLOR_BLANCO,
                    },
                },
            }

            valor_buscador = st_searchbox(
                opciones_autocompletado,
                inventario=inventario,
                placeholder="Escribe al menos 3 caracteres, por ejemplo: Sol",
                key="buscador_carta_dinamico",
                default_use_searchterm=True,
                clear_on_submit=False,
                edit_after_submit="current",
                debounce=180,
                rerun_on_update=True,
                style_overrides=estilo_buscador,
            )

        if isinstance(valor_buscador, dict):
            accion_buscador = valor_buscador.get(
                "accion",
                "seleccionar_carta",
            )

            if accion_buscador == "mostrar_sugerencias":
                termino_actual = valor_buscador.get("termino", "").strip()
                sugerencias_actuales = Buscar_sugerencias(
                    inventario,
                    termino_actual,
                    limite=12,
                )
                resultados_previsualizacion = resultados_desde_sugerencias(
                    sugerencias_actuales,
                    inventario,
                )
                previsualizacion_activa = True
            else:
                nombre_seleccionado = valor_buscador.get("nombre", "")
                coincidencias = Filtrar_inventario_por_nombre(
                    inventario,
                    nombre_seleccionado,
                )
                resultados_seleccionados = [
                    preparar_resultado(
                        carta,
                        nombre_seleccionado,
                    )
                    for carta in coincidencias
                ]
                resultados_seleccionados.sort(
                    key=lambda fila: (
                        fila["Edición"].casefold(),
                        fila["Acabado"].casefold(),
                    )
                )
                guardar_busqueda(
                    resultados=resultados_seleccionados,
                    no_encontradas=(
                        [] if resultados_seleccionados
                        else [nombre_seleccionado]
                    ),
                    total_buscado=1,
                    total_encontrado=(
                        1 if resultados_seleccionados else 0
                    ),
                    tipo_busqueda="carta",
                )
                st.session_state["ultima_vista_resultados"] = "Buscar cartas"
                st.rerun()

        elif isinstance(valor_buscador, str):
            termino_actual = valor_buscador.strip()
            termino_normalizado = normalizar_nombre(termino_actual)

            if termino_actual and len(termino_normalizado) < 3:
                st.caption(
                    "Escribe al menos 3 caracteres para ver sugerencias."
                )

            if len(termino_normalizado) >= 3:
                previsualizacion_activa = True
                sugerencias_actuales = Buscar_sugerencias(
                    inventario,
                    termino_actual,
                    limite=12,
                )
                resultados_previsualizacion = resultados_desde_sugerencias(
                    sugerencias_actuales,
                    inventario,
                )

    with columna_resumen:
        tarjeta_metrica(
            "Precio referencia Cardkingdom",
            f"$1 USD = ${VALOR_CK_CLP} CLP",
            "Conversión fija utilizada por la tienda",
            clase_extra="desktop-only",
        )

        if previsualizacion_activa:
            tarjeta_metrica(
                "Sugerencias",
                str(len(resultados_previsualizacion)),
                (
                    "Se han encontrado cartas."
                    if resultados_previsualizacion
                    else "Prueba con otro nombre de carta."
                ),
            )

# ============================================================
# VISTA 2: LISTA DE CARTAS
# ============================================================

elif vista_principal == "Lista de cartas":
    st.markdown(
        '<div class="section-title results-title">Lista de cartas</div>',
        unsafe_allow_html=True,
    )

    with st.form("formulario_busqueda_lista"):
        texto_lista = st.text_area(
            "Lista de cartas",
            placeholder=(
                "Puedes pegar listas desde Manabox, Moxfield o texto:\n\n"
                "1 Beast Whisperer (CLU) 158\n"
                "3 Aclazotz, Deepest Betrayal / Temple of the Dead (LCI) 88\n"
                "2 Ad Nauseam\n"
                "1 Sol Ring"
            ),
            height=250,
            label_visibility="collapsed",
        )

        enviar_lista = st.form_submit_button(
            "Buscar cartas",
            type="primary",
            use_container_width=True,
        )

    if enviar_lista:
        cartas_buscadas = parsear_lista_cartas(texto_lista)

        if not cartas_buscadas:
            for clave in (
                "resultados",
                "no_encontradas",
                "total_buscado",
                "total_encontrado",
                "tipo_busqueda_realizada",
            ):
                st.session_state.pop(clave, None)

            st.markdown(
                """
                <div class="empty-warning">
                    Pega al menos una carta para comenzar la búsqueda.
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            with st.spinner("Buscando coincidencias..."):
                resultados, no_encontradas = buscar_lista_en_inventario(
                    cartas_buscadas,
                    inventario,
                )

            total_lineas = len(cartas_buscadas)
            lineas_encontradas = total_lineas - len(no_encontradas)

            guardar_busqueda(
                resultados=resultados,
                no_encontradas=no_encontradas,
                total_buscado=total_lineas,
                total_encontrado=lineas_encontradas,
                tipo_busqueda="lista",
            )
            st.session_state["ultima_vista_resultados"] = "Lista de cartas"
            st.rerun()

# ============================================================
# RESULTADOS GUARDADOS / FIJOS
# ============================================================

if vista_principal in ("Buscar cartas", "Lista de cartas"):
    busqueda_guardada = "resultados" in st.session_state

    if previsualizacion_activa or busqueda_guardada:
        if previsualizacion_activa:
            resultados = resultados_previsualizacion
            no_encontradas = []
            tipo_resultado_actual = "sugerencias"
        else:
            resultados = st.session_state.get("resultados", [])
            no_encontradas = st.session_state.get("no_encontradas", [])
            tipo_resultado_actual = st.session_state.get(
                "tipo_busqueda_realizada",
                "",
            )

        st.markdown(
            '<div class="section-title results-title">'
            'Resultados de la búsqueda'
            '</div>',
            unsafe_allow_html=True,
        )

        if resultados:
            render_tarjetas(resultados, "busqueda")
        else:
            st.info("No se encontraron coincidencias en el stock actual.")

        if tipo_resultado_actual == "lista":
            st.markdown(
                '<div class="section-title">Cartas no encontradas</div>',
                unsafe_allow_html=True,
            )

            if no_encontradas:
                st.markdown(
                    "\n".join(
                        f"- {nombre}" for nombre in no_encontradas
                    )
                )
            else:
                st.success(
                    "Todas las cartas solicitadas fueron encontradas."
                )

# ============================================================
# VISTA 3: CARRITO
# ============================================================

elif vista_principal == "Carrito":
    render_carrito_completo()
