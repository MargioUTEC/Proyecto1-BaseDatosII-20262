-- Demo del Bloque B: recuperación de texto sobre el corpus de expedientes.
--
--   python scripts/generar_expedientes.py          (crea data/expedientes.csv)
--   python -m queryengine -f scripts/sql/textual.sql --plan
--
-- o pegando las sentencias en la consola web (también están como recetas).

-- 1. Tabla de expedientes. cuerpo es el texto libre que se indexa.
CREATE TABLE expedientes (
  id         INT PRIMARY KEY,
  persona_id INT,
  tipo       CHAR(32),
  sede       CHAR(24),
  fecha      DATE,
  cuerpo     VARCHAR(1000)
) USING HEAP;

COPY expedientes FROM 'expedientes.csv';

-- 2. Sin índice: FullTextScan lee todas las páginas y analiza cada texto.
SELECT id, tipo, COSINE_SCORE() AS relevancia FROM expedientes
 WHERE cuerpo MATCH 'camara falla iluminacion'
 ORDER BY relevancia DESC LIMIT 10;

-- 3. Índice invertido en disco construido con SPIMI:
--    vocab.dic + postings.inv + docs.bin en <tablas>/ix_cuerpo.inv/
CREATE INDEX ix_cuerpo ON expedientes(cuerpo) USING INVERTED
  WITH (scoring = 'TFIDF_COSINE', language = 'spanish');

-- 4. La misma consulta: InvertedIndexScan, mismos resultados, pocas lecturas.
SELECT id, tipo, COSINE_SCORE() AS relevancia FROM expedientes
 WHERE cuerpo MATCH 'camara falla iluminacion'
 ORDER BY relevancia DESC LIMIT 10;

-- 5. Booleanas con mezcla lineal de listas de posteo (AND, OR, AND-NOT).
SELECT id, tipo FROM expedientes
 WHERE cuerpo MATCH 'verificacion AND rostro AND NOT mascarilla';

SELECT id, tipo FROM expedientes
 WHERE cuerpo MATCH '(mochila OR laptop) AND extraviado';

-- 6. Híbrida: el MATCH maneja el acceso, el filtro relacional va después
--    (post-filtering sobre los candidatos rankeados).
SELECT id, sede, COSINE_SCORE() AS relevancia FROM expedientes
 WHERE cuerpo MATCH 'credencial vencida duplicado' AND sede = 'Miraflores'
 ORDER BY relevancia DESC LIMIT 5;

-- 7. El plan sin ejecutar.
EXPLAIN SELECT id FROM expedientes WHERE cuerpo MATCH 'alarma AND sospechoso';
