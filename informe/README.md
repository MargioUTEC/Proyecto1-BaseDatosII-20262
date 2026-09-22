# Informe del Entregable 1

`informe.tex` es el documento completo. Se compila con cualquier distribución de
LaTeX:

```bash
cd informe
tectonic -X compile informe.tex          # o: pdflatex informe.tex (dos pasadas)
```

Hace falta **`UTEC-Logo.jpg`** en esta misma carpeta para la portada. No está
versionado; ponlo antes de compilar o comenta la línea `\includegraphics` de la
portada si quieres una prueba rápida.

## Estado de las secciones

| Sección | Estado |
|---|---|
| 1. Introducción y arquitectura | escrita |
| 2. Capa de almacenamiento físico | escrita |
| 3. Métodos de organización de archivos | pendiente |
| 4.1 Árbol B+ en disco | escrita |
| 4.2 Hashing dinámico | pendiente |
| 5. Motor de consultas, parser SQL y telemetría | escrita |
| 6. Experimentación y evaluación | escrita |
| 7. Conclusiones | escrita |
| Anexos | escritos |

Las secciones pendientes están en el archivo como comentarios con los puntos que
el enunciado pide cubrir.

## De dónde salen los números

Todas las cifras de la sección 6 son medidas, no estimadas, y vienen de
`benchmarks/results/`. Para regenerarlas:

```bash
python benchmarks/experiments.py --backend disk
```

Las alturas del árbol B+ por tamaño de bloque de la tabla 11 se midieron aparte,
cargando cien mil claves con cada valor de `PAGE_SIZE` y descendiendo el árbol
hasta una hoja.

## Sobre las figuras

Los cinco gráficos se dibujan con `pgfplots` dentro del propio documento, no se
incluyen como imágenes. Así salen vectoriales, usan la tipografía del informe y
no hay archivos que se desincronicen de los datos.

Cada estructura tiene un color fijo en todas las figuras (el árbol B+ siempre
ámbar, el Sequential siempre verde azulado), y además cada serie lleva su propio
marcador, de modo que las figuras se siguen distinguiendo impresas en blanco y
negro. La paleta se validó para visión con deficiencia cromática.

El experimento 2 usa un gráfico de puntos y no de barras: el eje es logarítmico
y en una barra el valor lo codifica la longitud, proporción que se pierde al
comprimir tres órdenes de magnitud.
