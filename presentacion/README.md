# Presentación

Diseño en Canva: <https://www.canva.com/design/DAHV8NZfwAk/fICRGdefZQql3ZPaZZsfvA/edit>

Las diapositivas 7 a 14 quedaron escritas con el texto definitivo. Falta
**arrastrar las imágenes** de `imagenes/` sobre el marco que cada una ya trae.
El pie de cada diapositiva dice qué archivo va.

| Diapositiva | Título | Imagen |
|---|---|---|
| 7 | Motor de consultas | `08-pipeline.png` |
| 8 | Por donde busca | `09-rutas.png` |
| 9 | Sin indice | `07-consola-escaneo.jpg` |
| 10 | Con indice | `06-consola-indice.jpg` |
| 11 | Insercion masiva | `d02-e1-escrituras.png` |
| 12 | Busqueda puntual | `d03-e2-puntuales.png` |
| 13 | Cuando deja de servir | `d04-e3-selectividad.png` |
| 14 | Tamano de bloque | `d05-e4-bloque.png` |

Para reemplazar: arrastra el archivo desde el Finder sobre la imagen que ya
está en la diapositiva. Canva la sustituye conservando tamaño y posición.

## De dónde salen

- `06` y `07` son capturas de la consola en tema oscuro, con los 100 000
  clientes cargados. Son medidas reales, no montajes.
- `08` y `09` son diagramas dibujados con TikZ.
- Las que empiezan con `d` son las figuras del informe, regeneradas con fondo
  oscuro para que asienten sobre el fondo de la plantilla.
- `d01-e1-tiempo.png` no se usa en ninguna diapositiva; está por si quieren
  mostrar el tiempo además de las escrituras.

Todas usan el mismo color por estructura que el informe: ámbar para el árbol
B+, verde azulado para el Sequential File, índigo para el Heap. La paleta se
validó para visión con deficiencia cromática, y cada serie lleva su propio
marcador para que se distinga también impresa en blanco y negro.

Para regenerarlas, las figuras salen de `informe/informe.tex` y los diagramas
del bloque TikZ que quedó en el historial de esta carpeta.
