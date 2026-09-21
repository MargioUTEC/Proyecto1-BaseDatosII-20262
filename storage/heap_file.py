import struct
from disk_management import DiskManager

# Cabecera (20 bytes): page_id, record_count, free_space, next_page_id, prev_page_id
NODE_HEADER_FORMAT = "<iiiii"
NODE_HEADER_SIZE = struct.calcsize(NODE_HEADER_FORMAT)

# Tupla (66 bytes): id(int), nombre(30 chars), dept(20 chars), salario(float), next_page(int), next_slot(int)
RECORD_FORMAT = "<i 30s 20s f i i"
RECORD_SIZE = struct.calcsize(RECORD_FORMAT)

def clean_str(b_str: bytes) -> str:
    return b_str.decode('utf-8', errors='ignore').rstrip('\x00')

def pack_str(s: str, length: int) -> bytes:
    return s.encode('utf-8')[:length].ljust(length, b'\x00')


class HeapFile:
    def __init__(self, disk_manager: DiskManager):
        self.dm = disk_manager
        self.page_size = self.dm.page_size
        self.max_records = (self.page_size - NODE_HEADER_SIZE) // RECORD_SIZE
        
        # Inicializar la primera pagina si el archivo esta vacio
        if self.dm.get_total_pages() == 0:
            first_page_id = self.dm.allocate_page()
            empty_node = self._create_empty_node(first_page_id)
            self._write_node(empty_node)

    def _create_empty_node(self, page_id: int, prev_id: int = -1, next_id: int = -1) -> dict:
        return {
            'id': page_id,
            'count': 0,
            'free_space': self.page_size - NODE_HEADER_SIZE,
            'next': next_id,
            'prev': prev_id,
            'records': []
        }

    def _read_node(self, page_id: int) -> dict:
        # Convierte una pagina fisica de disco a un diccionario en memoria
        if page_id == -1:
            return None
            
        data = self.dm.read_page(page_id)
        pid, count, free, nxt, prev = struct.unpack_from(NODE_HEADER_FORMAT, data, 0)
        
        node = {
            'id': pid,
            'count': count,
            'free_space': free,
            'next': nxt,
            'prev': prev,
            'records': []
        }
        
        offset = NODE_HEADER_SIZE
        for _ in range(count):
            r = struct.unpack_from(RECORD_FORMAT, data, offset)
            node['records'].append({
                'id': r[0],
                'nombre': clean_str(r[1]),
                'dept': clean_str(r[2]),
                'salario': r[3],
                'next_page': r[4],
                'next_slot': r[5]
            })
            offset += RECORD_SIZE
            
        return node

    def _write_node(self, node: dict):
        # Convierte un diccionario en memoria a un bytearray exacto y lo escribe en disco
        data = bytearray(self.page_size)
        struct.pack_into(
            NODE_HEADER_FORMAT, data, 0,
            node['id'], node['count'], node['free_space'],
            node['next'], node['prev']
        )
        
        offset = NODE_HEADER_SIZE
        for rec in node['records']:
            struct.pack_into(
                RECORD_FORMAT, data, offset,
                rec['id'], pack_str(rec['nombre'], 30), pack_str(rec['dept'], 20),
                rec['salario'], rec.get('next_page', -1), rec.get('next_slot', -1)
            )
            offset += RECORD_SIZE
            
        self.dm.write_page(node['id'], bytes(data))

    def insert(self, record_dict: dict):
        record_dict['next_page'] = -1
        record_dict['next_slot'] = -1
        
        last_page_id = self.dm.get_total_pages() - 1
        node = self._read_node(last_page_id)
        
        # Caso 1: La ultima pagina tiene espacio
        if node['count'] < self.max_records:
            node['records'].append(record_dict)
            node['count'] += 1
            node['free_space'] -= RECORD_SIZE
            self._write_node(node)
        
        # Caso 2: La ultima pagina esta llena, solicitar un nuevo bloque
        else:
            new_page_id = self.dm.allocate_page()
            new_node = self._create_empty_node(new_page_id, prev_id=node['id'])
            
            new_node['records'].append(record_dict)
            new_node['count'] = 1
            new_node['free_space'] -= RECORD_SIZE
            
            node['next'] = new_page_id
            
            self._write_node(node)
            self._write_node(new_node)

    def search(self, target_id: int):
        # Busqueda mediante escaneo lineal exhaustivo (O(N) bloques leidos)
        total_pages = self.dm.get_total_pages()
        for page_id in range(total_pages):
            node = self._read_node(page_id)
            if not node or node['count'] == 0:
                continue
            
            for idx, rec in enumerate(node['records']):
                if rec['id'] == target_id:
                    return (page_id, idx), rec
                    
        return None

    def remove(self, target_id: int) -> bool:
        # 1. Buscar la ubicacion del registro a eliminar
        target_loc = self.search(target_id)
        if not target_loc:
            return False
            
        (target_page_id, target_idx), _ = target_loc
        
        # 2. Localizar la ultima pagina y el ultimo registro absoluto en el archivo
        last_page_id = self.dm.get_total_pages() - 1
        last_node = self._read_node(last_page_id)
        
        # Retroceder en caso de que las ultimas paginas hayan quedado vacias temporalmente
        while last_node and last_node['count'] == 0 and last_page_id > 0:
            last_page_id -= 1
            last_node = self._read_node(last_page_id)
            
        if not last_node or last_node['count'] == 0:
            return False
            
        last_idx = last_node['count'] - 1
        last_record = last_node['records'][last_idx]
        
        # 3. Move-the-last
        if target_page_id == last_page_id and target_idx == last_idx:
            # El objetivo es el ultimo, simplemente eliminarlo
            last_node['records'].pop()
            last_node['count'] -= 1
            last_node['free_space'] += RECORD_SIZE
            self._write_node(last_node)
        elif target_page_id == last_page_id:
            # Ambos registros estan en la misma pagina, pero el objetivo no es el ultimo
            last_node['records'][target_idx] = last_record
            last_node['records'].pop()
            last_node['count'] -= 1
            last_node['free_space'] += RECORD_SIZE
            self._write_node(last_node)
        else:
            # Si estan en paginas diferentes, sobreescribir el objetivo y limpiar el origen
            target_node = self._read_node(target_page_id)
            target_node['records'][target_idx] = last_record
            self._write_node(target_node)
            
            last_node['records'].pop()
            last_node['count'] -= 1
            last_node['free_space'] += RECORD_SIZE
            self._write_node(last_node)
            
        return True