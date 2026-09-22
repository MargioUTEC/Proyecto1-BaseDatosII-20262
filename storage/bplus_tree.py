import struct
from disk_management import DiskManager

# Cabecera del Nodo (20 bytes):
# page_id (4), is_leaf (4), num_keys (4), next_page_id (4), prev_page_id (4)
# Usamos little-endian "<" para ser consistentes con page.py
NODE_HEADER_FORMAT = "<iiiii"
NODE_HEADER_SIZE = struct.calcsize(NODE_HEADER_FORMAT)

# Cabecera para la página 0 (Metadata del árbol)
META_HEADER_FORMAT = "<i"

class BPlusTree:
    def __init__(self, disk_manager: DiskManager):
        # 1. INYECCIÓN DE DEPENDENCIAS: El árbol usa el DiskManager oficial
        self.dm = disk_manager
        self.page_size = self.dm.page_size
        
        self.LEAF_ENTRY_SIZE = 12 # Key(4) + PageID(4) + SlotID(4)
        
        # 2. CÁLCULO DINÁMICO DE CAPACIDAD basado en el tamaño de bloque real
        self.MAX_LEAF_KEYS = (self.page_size - NODE_HEADER_SIZE) // self.LEAF_ENTRY_SIZE
        self.MAX_INTERNAL_KEYS = (self.page_size - NODE_HEADER_SIZE - 4) // 8
        
        # 3. ALINEACIÓN DE PÁGINAS: Si el archivo está vacío, creamos la Página 0
        # La Página 0 será exclusivamente para guardar el root_id.
        if self.dm.get_total_pages() == 0:
            meta_page_id = self.dm.allocate_page() # Asigna la página 0
            self._write_root(-1)

    def _read_root(self):
        """Lee el root_id desde la Página 0 (Página de Metadata)"""
        data = self.dm.read_page(0)
        return struct.unpack_from(META_HEADER_FORMAT, data, 0)[0]

    def _write_root(self, root_id):
        """Guarda el root_id en la Página 0"""
        data = bytearray(self.page_size)
        struct.pack_into(META_HEADER_FORMAT, data, 0, root_id)
        self.dm.write_page(0, bytes(data))

    def _allocate_node(self, is_leaf):
        """Pide un nuevo bloque físico al DiskManager y prepara el diccionario del nodo"""
        new_id = self.dm.allocate_page()
        
        node = {
            'id': new_id, 
            'is_leaf': is_leaf, 
            'num_keys': 0, 
            'next_page_id': -1, 
            'prev_page_id': -1
        }
        if is_leaf:
            node['entries'] = [] 
        else:
            node['keys'] = []
            node['children'] = []
        return node

    def _read_node(self, page_id):
        """Lee una página física del disco y la deserializa a un diccionario (Nodo)"""
        if page_id == -1: return None
        
        # Lectura mediante DiskManager 
        data = self.dm.read_page(page_id)
        
        pid, is_leaf, num_keys, next_p, prev_p = struct.unpack_from(NODE_HEADER_FORMAT, data, 0)
        node = {
            'id': pid, 'is_leaf': is_leaf, 'num_keys': num_keys, 
            'next_page_id': next_p, 'prev_page_id': prev_p
        }
        
        if is_leaf:
            entries = []
            offset = NODE_HEADER_SIZE
            for _ in range(num_keys):
                k, r_pid, r_sid = struct.unpack_from("<iii", data, offset)
                entries.append({'key': k, 'rid': (r_pid, r_sid)})
                offset += self.LEAF_ENTRY_SIZE
            node['entries'] = entries
        else:
            keys = []
            offset = NODE_HEADER_SIZE
            for _ in range(num_keys):
                keys.append(struct.unpack_from("<i", data, offset)[0])
                offset += 4
                
            children = []
            # Los hijos se guardan en un espacio fijo después del arreglo máximo de claves
            # Esto evita solapamiento y mantiene el offset predecible.
            child_offset = NODE_HEADER_SIZE + (self.MAX_INTERNAL_KEYS * 4)
            for _ in range(num_keys + 1):
                children.append(struct.unpack_from("<i", data, child_offset)[0])
                child_offset += 4
                
            node['keys'] = keys
            node['children'] = children
            
        return node

    def _write_node(self, node):
        """Empaqueta el nodo en un bloque exacto de 4096 bytes y lo escribe a disco"""
        data = bytearray(self.page_size)
        
        # 1. Escribir Cabecera
        struct.pack_into(NODE_HEADER_FORMAT, data, 0, 
                         node['id'], node['is_leaf'], node['num_keys'], 
                         node['next_page_id'], node['prev_page_id'])
        
        # 2. Escribir Cuerpo
        if node['is_leaf']:
            offset = NODE_HEADER_SIZE
            for entry in node['entries']:
                struct.pack_into("<iii", data, offset, entry['key'], entry['rid'][0], entry['rid'][1])
                offset += self.LEAF_ENTRY_SIZE
        else:
            offset = NODE_HEADER_SIZE
            for key in node['keys']:
                struct.pack_into("<i", data, offset, key)
                offset += 4
                
            child_offset = NODE_HEADER_SIZE + (self.MAX_INTERNAL_KEYS * 4)
            for child in node['children']:
                struct.pack_into("<i", data, child_offset, child)
                child_offset += 4
                
        # Escritura oficial mediante DiskManager
        self.dm.write_page(node['id'], bytes(data))


    # =====================================================================
    # LÓGICA DEL ÁRBOL B+ (Split, Inserción, Búsqueda)
    # =====================================================================

    def insert(self, key, rid):
        root_id = self._read_root()
        if root_id == -1:
            root_node = self._allocate_node(is_leaf=1)
            root_node['entries'].append({'key': key, 'rid': rid})
            root_node['num_keys'] = 1
            self._write_node(root_node)
            self._write_root(root_node['id'])
            return

        promoted_key, promoted_right_child = self._insert_rec(root_id, key, rid)
        
        if promoted_key is not None:
            new_root = self._allocate_node(is_leaf=0)
            new_root['keys'] = [promoted_key]
            new_root['children'] = [root_id, promoted_right_child]
            new_root['num_keys'] = 1
            self._write_node(new_root)
            self._write_root(new_root['id'])

    def _insert_rec(self, node_id, key, rid):
        node = self._read_node(node_id)
        
        if node['is_leaf']:
            entries = node['entries']
            inserted = False
            for i, e in enumerate(entries):
                if e['key'] == key: return None, None
                if e['key'] > key:
                    entries.insert(i, {'key': key, 'rid': rid})
                    inserted = True
                    break
            if not inserted:
                entries.append({'key': key, 'rid': rid})
            
            node['num_keys'] = len(entries)
            
            if node['num_keys'] <= self.MAX_LEAF_KEYS:
                self._write_node(node)
                return None, None
            
            # Realizar Split 50%
            split_idx = node['num_keys'] // 2
            new_leaf = self._allocate_node(is_leaf=1)
            new_leaf['entries'] = entries[split_idx:]
            new_leaf['num_keys'] = len(new_leaf['entries'])
            
            node['entries'] = entries[:split_idx]
            node['num_keys'] = len(node['entries'])
            
            # Ajuste de Punteros horizontales (Doble enlace)
            new_leaf['next_page_id'] = node['next_page_id']
            new_leaf['prev_page_id'] = node['id']
            
            if node['next_page_id'] != -1:
                next_node = self._read_node(node['next_page_id'])
                next_node['prev_page_id'] = new_leaf['id']
                self._write_node(next_node)
                
            node['next_page_id'] = new_leaf['id']
            
            self._write_node(node)
            self._write_node(new_leaf)
            
            promoted_key = new_leaf['entries'][0]['key']
            return promoted_key, new_leaf['id']
            
        else:
            keys = node['keys']
            child_idx = len(keys)
            for i, k in enumerate(keys):
                if key < k:
                    child_idx = i
                    break
                    
            promoted_key, promoted_right = self._insert_rec(node['children'][child_idx], key, rid)
            
            if promoted_key is None: return None, None
                
            node['keys'].insert(child_idx, promoted_key)
            node['children'].insert(child_idx + 1, promoted_right)
            node['num_keys'] = len(node['keys'])
            
            if node['num_keys'] <= self.MAX_INTERNAL_KEYS:
                self._write_node(node)
                return None, None
                
            # Split de nodo interno
            split_idx = node['num_keys'] // 2
            promoted_up_key = node['keys'][split_idx]
            
            new_internal = self._allocate_node(is_leaf=0)
            new_internal['keys'] = node['keys'][split_idx + 1:]
            new_internal['children'] = node['children'][split_idx + 1:]
            new_internal['num_keys'] = len(new_internal['keys'])
            
            node['keys'] = node['keys'][:split_idx]
            node['children'] = node['children'][:split_idx + 1]
            node['num_keys'] = len(node['keys'])
            
            self._write_node(node)
            self._write_node(new_internal)
            
            return promoted_up_key, new_internal['id']

    def search(self, key):
        root_id = self._read_root()
        if root_id == -1: return None
        
        curr_node = self._read_node(root_id)
        while not curr_node['is_leaf']:
            idx = len(curr_node['keys']) 
            for i, k in enumerate(curr_node['keys']):
                if key < k:
                    idx = i
                    break
            curr_node = self._read_node(curr_node['children'][idx])
            
        for entry in curr_node['entries']:
            if entry['key'] == key:
                return entry['rid']
        return None 

    def rangeSearch(self, init_key, end_key):
        results = [] 
        root_id = self._read_root()
        if root_id == -1: return results
        
        curr_node = self._read_node(root_id)
        while not curr_node['is_leaf']:
            idx = len(curr_node['keys'])
            for i, k in enumerate(curr_node['keys']):
                if init_key < k:
                    idx = i
                    break
            curr_node = self._read_node(curr_node['children'][idx])
            
        while curr_node is not None:
            for entry in curr_node['entries']:
                if entry['key'] > end_key:
                    return results
                if init_key <= entry['key'] <= end_key:
                    results.append(entry['rid'])
            
            if curr_node['next_page_id'] != -1:
                curr_node = self._read_node(curr_node['next_page_id'])
            else:
                curr_node = None
        return results

    def remove(self, key):
        root_id = self._read_root()
        if root_id == -1: return False
        
        curr_node = self._read_node(root_id)
        
        while not curr_node['is_leaf']:
            idx = len(curr_node['keys'])
            for i, k in enumerate(curr_node['keys']):
                if key < k:
                    idx = i
                    break
            curr_node = self._read_node(curr_node['children'][idx])
            
        entries = curr_node['entries']
        for i, entry in enumerate(entries):
            if entry['key'] == key:
                entries.pop(i)
                curr_node['num_keys'] -= 1
                self._write_node(curr_node)
                return True 
                
        return False
