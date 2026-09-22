# Datos

Dataset del proyecto: **Customers** de
[Datablist](https://www.datablist.com/learn/csv/download-sample-csv-files#customers-dataset).

Los CSV no estan versionados porque pesan entre 17 MB y 400 MB. Para traerlos:

```bash
./scripts/fetch_dataset.sh              # 100 000 y 500 000 registros
./scripts/fetch_dataset.sh 1000000      # un tamano concreto
```

## Esquema

| Columna | Tipo | Ancho maximo observado |
|---|---|---|
| `Index` | INT | clave primaria, 1..N |
| `Customer Id` | CHAR(15) | 15 |
| `First Name` | CHAR(16) | 11 |
| `Last Name` | CHAR(16) | 11 |
| `Company` | CHAR(40) | 36 |
| `City` | CHAR(28) | 24 |
| `Country` | CHAR(56) | 51 |
| `Phone 1` | CHAR(24) | 22 |
| `Phone 2` | CHAR(24) | 22 |
| `Email` | CHAR(48) | 44 |
| `Subscription Date` | DATE | 10 |
| `Website` | CHAR(44) | 39 |

`Index` es entero y unico, asi que sirve como `PRIMARY KEY` y es la columna que
los indices en disco aceptan: tanto el arbol B+ como el hash extensible
empaquetan claves enteras.

Los anchos de arriba salen de medir el archivo de 100 000 registros; estan
redondeados hacia arriba para dejar margen.
