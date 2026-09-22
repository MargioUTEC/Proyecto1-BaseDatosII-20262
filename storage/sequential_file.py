import os
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


class SequentialFile:
    def __init__(self, main_dm: DiskManager, overflow_dm: DiskManager):
        self.main_dm = main_dm
        self.overflow_dm = overflow_dm
        self.page_size = self.main_dm.page_size
        self.max_records = (self.page_size - NODE_HEADER_SIZE) // RECORD_SIZE
        
        # Inicializar archivos si estan vacios
        if self.main_dm.get_total_pages() == 0:
            pid = self.main_dm.allocate_page()
            self._write_node(self.main_dm, self._create_empty_node(pid))
            
        if self.overflow_dm.get_total_pages() == 0:
            pid = self.overflow_dm.allocate_page()
            self._write_node(self.overflow_dm, self._create_empty_node(pid))

    def _create_empty_node(self, page_id: int, prev_id: int = -1, next_id: int = -1) -> dict:
        return {
            'id': page_id,
            'count': 0,
            'free_space': NODE_HEADER_SIZE,
            'next': next_id,
            'prev': prev_id,
            'records': []
        }

    def _read_node(self, dm: DiskManager, page_id: int) -> dict:
        if page_id == -1:
            return None
        data = dm.read_page(page_id)
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

    def _write_node(self, dm: DiskManager, node: dict):
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
        dm.write_page(node['id'], bytes(data))

    def _binary_search_page(self, target_id: int) -> int:
        total_pages = self.main_dm.get_total_pages()
        if total_pages == 0:
            return 0
            
        L = 0
        R = total_pages - 1
        target_page_idx = 0
        
        while L <= R:
            mid = (L + R) // 2
            node = self._read_node(self.main_dm, mid)
            
            if node['count'] == 0:
                R = mid - 1
                continue
                
            first_key = node['records'][0]['id']
            if first_key <= target_id:
                target_page_idx = mid
                L = mid + 1
            else:
                R = mid - 1
                
        return target_page_idx

    def search(self, target_id: int):
        page_id = self._binary_search_page(target_id)
        node = self._read_node(self.main_dm, page_id)
        
        if not node or node['count'] == 0:
            return None
            
        candidate_overflow = (-1, -1)
        
        # 1. Buscar en la pagina principal
        for idx, rec in enumerate(node['records']):
            if rec['id'] == target_id:
                return (node['id'], idx), rec
            if rec['id'] < target_id:
                candidate_overflow = (rec['next_page'], rec['next_slot'])
                
        # 2. Si no esta en la pagina principal, seguir el encadenamiento de overflow
        curr_page, curr_slot = candidate_overflow
        while curr_page != -1:
            ov_node = self._read_node(self.overflow_dm, curr_page)
            if not ov_node or curr_slot >= ov_node['count']:
                break
            rec = ov_node['records'][curr_slot]
            if rec['id'] == target_id:
                return (curr_page, curr_slot), rec
            curr_page, curr_slot = rec['next_page'], rec['next_slot']
            
        return None

    def insert(self, record_dict: dict):
        record_dict['next_page'] = -1
        record_dict['next_slot'] = -1
        
        target_page_id = self._binary_search_page(record_dict['id'])
        node = self._read_node(self.main_dm, target_page_id)
        
        # Caso 1: Hay espacio en la pagina del area principal
        if node['count'] < self.max_records:
            node['records'].append(record_dict)
            node['records'].sort(key=lambda x: x['id'])
            node['count'] = len(node['records'])
            node['free_space'] = NODE_HEADER_SIZE + (node['count'] * RECORD_SIZE)
            self._write_node(self.main_dm, node)
            return

        # Caso 2: La pagina esta llena, enviar al overflow
        ov_page_id = self.overflow_dm.get_total_pages() - 1
        ov_node = self._read_node(self.overflow_dm, ov_page_id)
        
        if ov_node['count'] >= self.max_records:
            new_ov_id = self.overflow_dm.allocate_page()
            ov_node = self._create_empty_node(new_ov_id, prev_id=ov_node['id'])
            
        inserted_slot = ov_node['count']
        inserted_page = ov_node['id']
        
        pred_idx = -1
        for idx, r in enumerate(node['records']):
            if r['id'] < record_dict['id']:
                pred_idx = idx
            else:
                break
                
        if pred_idx == -1:
            pred_idx = 0
            
        pred_rec = node['records'][pred_idx]
        
        record_dict['next_page'] = pred_rec['next_page']
        record_dict['next_slot'] = pred_rec['next_slot']
        
        pred_rec['next_page'] = inserted_page
        pred_rec['next_slot'] = inserted_slot
        
        ov_node['records'].append(record_dict)
        ov_node['count'] += 1
        ov_node['free_space'] += RECORD_SIZE
        
        self._write_node(self.main_dm, node)
        self._write_node(self.overflow_dm, ov_node)

    def reorganize(self):
        # Reorganizacion mediante mezcla externa
        # 1. Extraer y aislar los registros de overflow
        ov_records = []
        for p in range(self.overflow_dm.get_total_pages()):
            n = self._read_node(self.overflow_dm, p)
            if n:
                ov_records.extend(n['records'])
                
        for r in ov_records:
            r['next_page'] = -1
            r['next_slot'] = -1
        ov_records.sort(key=lambda x: x['id'])
        
        # 2. Generador que emite tuplas fusionadas
        def merge_generator():
            ov_idx = 0
            ov_len = len(ov_records)
            
            for p in range(self.main_dm.get_total_pages()):
                n = self._read_node(self.main_dm, p)
                if not n or n['count'] == 0:
                    continue
                    
                main_recs = n['records']
                m_idx = 0
                m_len = len(main_recs)
                
                while m_idx < m_len:
                    if ov_idx < ov_len and ov_records[ov_idx]['id'] < main_recs[m_idx]['id']:
                        yield ov_records[ov_idx]
                        ov_idx += 1
                    else:
                        # Limpiar punteros logicos al pasar a ser principales
                        main_recs[m_idx]['next_page'] = -1
                        main_recs[m_idx]['next_slot'] = -1
                        yield main_recs[m_idx]
                        m_idx += 1
                        
            # Si el archivo principal se vacea, emitir el resto del overflow
            while ov_idx < ov_len:
                yield ov_records[ov_idx]
                ov_idx += 1

        # 3. Empaquetar y volcar a disco en un archivo temporal
        FILL_FACTOR = max(1, int(self.max_records * 0.75))
        temp_path = self.main_dm.db_path + ".tmp"
        
        with open(temp_path, "wb") as temp_file:
            page_id = 0
            buffer = []
            
            iterator = merge_generator()
            try:
                next_rec = next(iterator)
            except StopIteration:
                next_rec = None
                
            while next_rec is not None:
                buffer.append(next_rec)
                
                try:
                    next_rec = next(iterator)
                except StopIteration:
                    next_rec = None
                    
                if len(buffer) == FILL_FACTOR or next_rec is None:
                    data = bytearray(self.page_size)
                    nxt = page_id + 1 if next_rec is not None else -1
                    prev = page_id - 1
                    
                    struct.pack_into(
                        NODE_HEADER_FORMAT, data, 0,
                        page_id, len(buffer), NODE_HEADER_SIZE + (len(buffer) * RECORD_SIZE),
                        nxt, prev
                    )
                    
                    offset = NODE_HEADER_SIZE
                    for r in buffer:
                        struct.pack_into(
                            RECORD_FORMAT, data, offset,
                            r['id'], pack_str(r['nombre'], 30), pack_str(r['dept'], 20),
                            r['salario'], r.get('next_page', -1), r.get('next_slot', -1)
                        )
                        offset += RECORD_SIZE
                        
                    temp_file.write(data)
                    page_id += 1
                    buffer = []

            # Si la base de datos estaba vacia, asegurar al menos una pagina
            if page_id == 0:
                temp_file.write(b"\x00" * self.page_size)

        # 4. Atomizar el sobreescribir
        os.replace(temp_path, self.main_dm.db_path)
        self.main_dm.counter.reset()
        
        # 5. Formatear area de overflow
        with open(self.overflow_dm.db_path, "wb") as f:
            f.write(b"\x00" * self.page_size)
        self.overflow_dm.counter.reset()
        init_ov_id = self.overflow_dm.allocate_page()
        self._write_node(self.overflow_dm, self._create_empty_node(init_ov_id))