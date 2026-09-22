-- Sensibilidad al tamano de bloque (Experimento 4).
--
-- El mismo esquema con B distinto: el fan-out se duplica al duplicar la pagina,
-- y una tabla con paginas grandes se recorre en menos transferencias.

CREATE TABLE b1024 ("Index" INT PRIMARY KEY, "Country" CHAR(56), "City" CHAR(28))
  WITH (PAGE_SIZE = 1024);
CREATE TABLE b4096 ("Index" INT PRIMARY KEY, "Country" CHAR(56), "City" CHAR(28))
  WITH (PAGE_SIZE = 4096);
CREATE TABLE b8192 ("Index" INT PRIMARY KEY, "Country" CHAR(56), "City" CHAR(28))
  WITH (PAGE_SIZE = 8192);

COPY b1024 FROM 'customers-10000.csv';
COPY b4096 FROM 'customers-10000.csv';
COPY b8192 FROM 'customers-10000.csv';

-- La misma busqueda en cada una. Mira las lecturas en el panel de costo:
-- a mayor bloque, menos transferencias para el mismo escaneo.
SELECT "Country" FROM b1024 WHERE "Index" = 9999;
SELECT "Country" FROM b4096 WHERE "Index" = 9999;
SELECT "Country" FROM b8192 WHERE "Index" = 9999;
