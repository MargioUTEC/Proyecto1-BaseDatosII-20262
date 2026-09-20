import os
import struct
from disk_management import DiskManager
from page import Page

DB_TEST_FILE = "test_customers.bin"

# Limpieza inicial para la prueba
if os.path.exists(DB_TEST_FILE):
  os.remove(DB_TEST_FILE)

print("=== 1. INICIALIZANDO DISK MANAGER ===")
dm = DiskManager(DB_TEST_FILE)

# 1. Asignar una nueva página en disco
page_id = dm.allocate_page()
print(f"Página asignada con ID: {page_id}")

# 2. Instanciar la página en memoria
page = Page(page_id=page_id)

# 3. Serializar 2 registros con struct (Simulando Customer: ID int (4B), Name char(20) (20B))
record_format = "<i20s"

rec1 = struct.pack(record_format, 101, b"Margiory Alvarado")
rec2 = struct.pack(record_format, 102, b"Diana Nanez")

# 4. Insertar registros en la página y obtener su RID
slot0 = page.insert_record(rec1)
rid0 = (page_id, slot0)
print(f"Registro 1 insertado con RID: {rid0}")

slot1 = page.insert_record(rec2)
rid1 = (page_id, slot1)
print(f"Registro 2 insertado con RID: {rid1}")

# 5. Persistir el bloque completo de 4096 bytes en disco
dm.write_page(page_id, page.to_bytes())
print(f"Bloque {page_id} escrito en disco exitosamente.")

print("\n=== 2. LEYENDO DIRECTAMENTE DESDE DISCO ===")
# 6. Leer el bloque físico desde el archivo
raw_block = dm.read_page(page_id)
loaded_page = Page(page_id=page_id, raw_bytes=raw_block)

print(
    f"Cabecera leída -> Page ID: {loaded_page.page_id}, Total registros:"
    f" {loaded_page.record_count}, Espacio libre restante:"
    f" {loaded_page.free_space_offset} bytes"
)

# 7. Recuperar registros por RID
rec1_bytes = loaded_page.get_record(rid0[1])
cust_id1, cust_name1 = struct.unpack(record_format, rec1_bytes)
print(
    f"Tupla recuperada en RID {rid0} -> ID: {cust_id1}, Nombre:"
    f" {cust_name1.decode('latin1').strip()}"
)

rec2_bytes = loaded_page.get_record(rid1[1])
cust_id2, cust_name2 = struct.unpack(record_format, rec2_bytes)
print(
    f"Tupla recuperada en RID {rid1} -> ID: {cust_id2}, Nombre:"
    f" {cust_name2.decode('latin1').strip()}"
)

print("\n=== 3. TELEMETRÍA DE I/O (DiskCounter) ===")
metrics = dm.counter.get_metrics()
print(f"Métricas registradas: {metrics}")
assert (
    metrics["disk_writes"] == 2
), "Debe haber 2 escrituras (allocate y write)"
assert metrics["disk_reads"] == 1, "Debe haber 1 lectura (read_page)"
print("¡TODAS LAS PRUEBAS PASARON CORRECTAMENTE!")