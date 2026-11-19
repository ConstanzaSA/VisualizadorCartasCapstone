# Tienda de Magic UC

La web de Streamlit se inicia con `streamlit run app.py`. El catálogo se lee
desde `Datos/inventario.xlsx`.

## Publicar el inventario del robot

1. Exporta el inventario **completo** del robot en formato `.xlsx`. Guarda el
   archivo como `Datos/inventario.xlsx` en el repositorio. Si el robot está en
   el mismo equipo, puedes configurar esa ruta como destino de su exportación.
2. En GitHub, abre la carpeta `Datos` en la rama que usa Streamlit, selecciona
   **Add file → Upload files** y sube el archivo con el mismo nombre. Confirma
   el commit en esa rama. Si trabajas desde un clon, puedes hacer lo mismo con
   estos comandos:

   ```sh
   git add -- Datos/inventario.xlsx
   git commit -m "Actualizar inventario"
   git push
   ```

3. Cuando Streamlit tenga el commit, abre la app y busca una carta cuyo stock
   haya cambiado. El botón ⟳ del título vuelve a leer la copia disponible en
   Streamlit.

Para instalar el cambio por primera vez, sube también `app.py` e
`inventario_excel.py` junto con el inventario. Las dependencias del lector ya
están en `requirements.txt`.

## Formato del Excel

La web usa la **primera hoja**, con encabezados en la primera fila.

| Columna | Uso |
| --- | --- |
| Nombre | Obligatoria. También admite `Carta`, `Name` o `Card Name`. |
| Cantidad | Entero no negativo. Admite `Quantity`, `Count`, `Stock` o `Qty`. Si falta el dato, se cuenta una copia por fila. El cero deja la fila fuera del catálogo. |
| Edición, Set, Collector Number, Scryfall ID, Acabado, Idioma | Opcionales. Permiten distinguir variantes de una carta. |
| Imagen URL, Tipo | Opcionales. Usa una URL de imagen accesible desde la web. |
| Precio CK USD | Opcional. Precio unitario numérico en USD para la conversión a CLP de la tienda. |
| Ubicación | Opcional. Se incluye en el Excel de compra para ubicar la carta. |

Las filas con la misma carta, variante, ubicación y precio se suman.
