import struct
from typing import Optional, Tuple
from config import PAGE_SIZE

# Formato cabecera: 5 enteros de 32 bits con signo (little-endian) = 20 bytes
# page_id, record_count, free_space_offset, next_page_id, prev_page_id
PAGE_HEADER_FORMAT = "<iiiii"
PAGE_HEADER_SIZE = struct.calcsize(PAGE_HEADER_FORMAT)  

# Formato de cada slot: offset (sin signo) y length (sin signo) = 4 bytes
SLOT_FORMAT = "<HH"
SLOT_SIZE = struct.calcsize(SLOT_FORMAT) 


class Page:

  def __init__(
      self,
      page_id: int,
      next_page_id: int = -1,
      prev_page_id: int = -1,
      raw_bytes: Optional[bytes] = None,
  ):
    if raw_bytes:
      if len(raw_bytes) != PAGE_SIZE:
        raise ValueError(
            f"El buffer raw_bytes debe tener tamaño {PAGE_SIZE} bytes"
        )
      self.data = bytearray(raw_bytes)
      (
          self.page_id,
          self.record_count,
          self.free_space_offset,
          self.next_page_id,
          self.prev_page_id,
      ) = struct.unpack_from(PAGE_HEADER_FORMAT, self.data, 0)
    else:
      self.data = bytearray(PAGE_SIZE)
      self.page_id = page_id
      self.record_count = 0
      self.free_space_offset = (
          PAGE_SIZE  # En slotted-page empieza en el extremo final (4096)
      )
      self.next_page_id = next_page_id
      self.prev_page_id = prev_page_id
      self._sync_header()

  def _sync_header(self):
    """Sincroniza los campos de la cabecera en los primeros 20 bytes del buffer."""
    struct.pack_into(
        PAGE_HEADER_FORMAT,
        self.data,
        0,
        self.page_id,
        self.record_count,
        self.free_space_offset,
        self.next_page_id,
        self.prev_page_id,
    )

  def insert_record(self, record_bytes: bytes) -> Optional[int]:
    """Inserta una tupla binaria en el bloque.

    Retorna el slot_number asignado, o None si no hay espacio suficiente.
    """
    rec_len = len(record_bytes)
    # Ubicación donde irá el nuevo slot 
    slot_offset = PAGE_HEADER_SIZE + (self.record_count * SLOT_SIZE)

    # Espacio libre requerido: 4 bytes para el slot + tamaño en bytes del registro
    required_space = SLOT_SIZE + rec_len

    if slot_offset + required_space > self.free_space_offset:
      return None  # Página llena

    # El nuevo registro se escribe hacia atrás desde el free_space_offset actual
    new_free_space = self.free_space_offset - rec_len
    self.data[new_free_space : self.free_space_offset] = record_bytes

    struct.pack_into("<HH", self.data, slot_offset, new_free_space, rec_len) #Registra el slot


    slot_number = self.record_count
    self.record_count += 1
    self.free_space_offset = new_free_space
    self._sync_header()

    return slot_number

  def get_record(self, slot_number: int) -> Optional[bytes]:
    """Recupera los bytes exactos de una tupla dado su número de slot."""
    if slot_number < 0 or slot_number >= self.record_count:
      return None

    slot_offset = PAGE_HEADER_SIZE + (slot_number * SLOT_SIZE)
    rec_offset, rec_len = struct.unpack_from("<HH", self.data, slot_offset)

    if rec_len == 0:
      return None  # Registro marcado como eliminado

    return bytes(self.data[rec_offset : rec_offset + rec_len])

  def to_bytes(self) -> bytes:
    return bytes(self.data)