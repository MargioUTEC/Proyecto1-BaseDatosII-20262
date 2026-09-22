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

  def __init__(self, db_path: str, page_size: int = PAGE_SIZE):
    """page_size permite variar B sin recompilar el módulo."""
    self.db_path = db_path
    self.page_size = page_size
    self.counter = DiskCounter()

    # Si no existe el archivo binario, se inicializa vacío
    if not os.path.exists(self.db_path):
      with open(self.db_path, "wb") as f:
        pass

  def read_page(self, page_id: int) -> bytes:
    """Lee exactamente 1 bloque con seek()"""
    offset = page_id * self.page_size

    if not os.path.exists(self.db_path):
      raise FileNotFoundError(f"Archivo no encontrado: {self.db_path}")

    file_size = os.path.getsize(self.db_path)
    if offset >= file_size:
      raise IndexError(
          f"Page ID {page_id} fuera de rango. Tamaño actual: {file_size} bytes")

    with open(self.db_path, "rb") as f:
      f.seek(offset)
      raw_bytes = f.read(self.page_size)

    # Telemetría obligatoria
    self.counter.disk_reads += 1

    # Asegura un bloque de tamaño exacto
    if len(raw_bytes) < self.page_size:
      raw_bytes = raw_bytes.ljust(self.page_size, b"\x00")

    return raw_bytes

  def write_page(self, page_id: int, page_bytes: bytes):
    """Escribe exactamente 1 bloque mediante seek()."""
    if len(page_bytes) != self.page_size:
      raise ValueError(
          f"El bloque debe medir {self.page_size} bytes (recibido:"
          f" {len(page_bytes)})"
      )

    offset = page_id * self.page_size

    # Abre en lectura/escritura binaria
    with open(self.db_path, "r+b") as f:
      f.seek(offset)
      f.write(page_bytes)
      f.flush()

    self.counter.disk_writes += 1

  def allocate_page(self) -> int:
    """Reserva una nueva página al final del archivo binario y retorna su page_id"""
    file_size = os.path.getsize(self.db_path)
    new_page_id = file_size // self.page_size

    # Escribe un bloque inicial en ceros. Modo r+b: en a+b el seek se ignora
    # y toda escritura va al final, lo que oculta errores de offset.
    empty_block = b"\x00" * self.page_size
    with open(self.db_path, "r+b") as f:
      f.seek(new_page_id * self.page_size)
      f.write(empty_block)
      f.flush()

    self.counter.disk_writes += 1
    return new_page_id

  def get_total_pages(self) -> int:
    return os.path.getsize(self.db_path) // self.page_size