import os
from config import PAGE_SIZE

class DiskCounter:

  def __init__(self):
    self.disk_reads = 0
    self.disk_writes = 0

  def reset(self):
    self.disk_reads = 0
    self.disk_writes = 0

  def get_metrics(self) -> dict:
    return {
        "disk_reads": self.disk_reads,
        "disk_writes": self.disk_writes,}


class DiskManager:

  def __init__(self, db_path: str):
    self.db_path = db_path
    self.counter = DiskCounter()

    # Si no existe el archivo binario, se inicializa vacío
    if not os.path.exists(self.db_path):
      with open(self.db_path, "wb") as f:
        pass

  def read_page(self, page_id: int) -> bytes:
    """Lee 1 bloque de 4096 bytes con seek()"""
    offset = page_id * PAGE_SIZE

    if not os.path.exists(self.db_path):
      raise FileNotFoundError(f"Archivo no encontrado: {self.db_path}")

    file_size = os.path.getsize(self.db_path)
    if offset >= file_size:
      raise IndexError(
          f"Page ID {page_id} fuera de rango. Tamaño actual: {file_size} bytes")

    with open(self.db_path, "rb") as f:
      f.seek(offset)
      raw_bytes = f.read(PAGE_SIZE)

    # Telemetría obligatoria
    self.counter.disk_reads += 1

    # Asegura bloque exacto de 4096 bytes
    if len(raw_bytes) < PAGE_SIZE:
      raw_bytes = raw_bytes.ljust(PAGE_SIZE, b"\x00")

    return raw_bytes

  def write_page(self, page_id: int, page_bytes: bytes):
    """Escribe exactamente 1 bloque de 4096 bytes mediante seek()."""
    if len(page_bytes) != PAGE_SIZE:
      raise ValueError(
          f"El bloque debe medir {PAGE_SIZE} bytes (recibido:"
          f" {len(page_bytes)})"
      )

    offset = page_id * PAGE_SIZE

    # Abre en lectura/escritura binaria
    with open(self.db_path, "r+b") as f:
      f.seek(offset)
      f.write(page_bytes)
      f.flush()

    self.counter.disk_writes += 1

  def allocate_page(self) -> int:
    """Reserva una nueva página al final del archivo binario y retorna su page_id"""
    file_size = os.path.getsize(self.db_path)
    new_page_id = file_size // PAGE_SIZE

    # Escribe un bloque inicial de 4096 bytes en ceros
    empty_block = b"\x00" * PAGE_SIZE
    with open(self.db_path, "a+b") as f:
      f.seek(new_page_id * PAGE_SIZE)
      f.write(empty_block)
      f.flush()

    self.counter.disk_writes += 1
    return new_page_id

  def get_total_pages(self) -> int:
    return os.path.getsize(self.db_path) // PAGE_SIZE