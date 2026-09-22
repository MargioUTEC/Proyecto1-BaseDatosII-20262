-- Guion de la demostracion.
--
--   python -m queryengine -f scripts/sql/demo.sql --plan
--
-- o pegando las sentencias una por una en la consola web. El dataset tiene una
-- columna llamada Index, que es palabra reservada, y otras con espacios: por eso
-- los nombres van entre comillas dobles.

-- 1. La tabla. El formato binario del registro sale de los tipos declarados.
CREATE TABLE customers (
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
) USING HEAP;

-- 2. Carga masiva. No pasa por el parser: parsear un INSERT de 100 000 tuplas
--    medirla el lexer, no el disco.
COPY customers FROM 'customers-100000.csv';

-- 3. Sin indice: la igualdad obliga a leer la tabla entera.
SELECT "First Name", "Last Name", "Country" FROM customers WHERE "Index" = 77777;

-- 4. Los dos indices en disco.
CREATE INDEX ix_btree ON customers("Index") USING BTREE;
CREATE INDEX ix_hash  ON customers("Index") USING HASH;

-- 5. La misma consulta de antes. Compara el panel de costo.
SELECT "First Name", "Last Name", "Country" FROM customers WHERE "Index" = 77777;

-- 6. Rango angosto: baja por el arbol y barre las hojas encadenadas.
SELECT "Index", "City", "Country" FROM customers WHERE "Index" BETWEEN 5000 AND 5010;

-- 7. Rango amplio: cada RID alcanzado por el indice cuesta una lectura suelta,
--    asi que pasado cierto punto el escaneo completo es mas barato y el
--    planificador vuelve a el.
SELECT "Index" FROM customers WHERE "Index" >= 1 AND "Index" <= 60000;

-- 8. El plan sin ejecutar la consulta.
EXPLAIN SELECT * FROM customers WHERE "Index" = 42;

-- 9. Un predicado que ningun indice cubre: filtro por encima del escaneo.
SELECT "First Name", "City" FROM customers
 WHERE "Country" = 'Peru' AND "Index" < 5000
 ORDER BY "City" LIMIT 10;

-- 10. Borrar mantiene los indices consistentes.
DELETE FROM customers WHERE "Index" = 77777;
SELECT "Index" FROM customers WHERE "Index" = 77777;
