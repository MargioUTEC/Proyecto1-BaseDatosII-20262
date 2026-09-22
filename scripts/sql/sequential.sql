-- Area ordenada, desbordamiento y reorganizacion.
--
--   python -m queryengine -f scripts/sql/sequential.sql --plan
--
-- Una tabla SEQUENTIAL mantiene dos archivos: <tabla>.bin con los registros
-- ordenados por clave primaria, y <tabla>.ovf con lo que se inserto despues.
-- Reorganizar mezcla ambos y reescribe el principal con fill factor 0,75.

CREATE TABLE clientes_ord (
  "Index"             INT PRIMARY KEY,
  "Customer Id"       CHAR(15),
  "First Name"        CHAR(16),
  "Last Name"         CHAR(16),
  "Company"           CHAR(40),
  "City"              CHAR(28),
  "Country"           CHAR(56),
  "Phone 1"           CHAR(24),
  "Phone 2"           CHAR(24),
  "Email"             CHAR(48),
  "Subscription Date" DATE,
  "Website"           CHAR(44)
) USING SEQUENTIAL;

COPY clientes_ord FROM 'customers-10000.csv';

-- Antes de reorganizar todo esta en el area de desbordamiento, que no esta
-- ordenada y se recorre entera. El planificador lo sabe y prefiere el escaneo
-- completo: una busqueda binaria sobre un area principal vacia no ahorraria
-- nada. Fijate en la ruta que elige.
SELECT "First Name", "City" FROM clientes_ord WHERE "Index" = 7777;

-- Ahora hay que reorganizar. Desde la consola web es el boton del panel de
-- tablas; por HTTP:
--     curl -XPOST localhost:8080/api/tables/reorganize \
--          -H 'Content-Type: application/json' -d '{"table":"clientes_ord"}'
-- Mezcla ambas areas, reescribe la principal en orden con fill factor 0,75 y
-- vacia el overflow.

-- La misma busqueda despues de reorganizar: ahora si baja por descenso binario
-- sobre las paginas ordenadas. Compara las lecturas con las de arriba.
SELECT "First Name", "City" FROM clientes_ord WHERE "Index" = 7777;

-- Un rango sobre una tabla agrupada son paginas contiguas, y por eso le gana
-- al arbol B+, que es un indice secundario y paga una lectura por cada RID.
SELECT "Index", "City" FROM clientes_ord WHERE "Index" BETWEEN 100 AND 140;
