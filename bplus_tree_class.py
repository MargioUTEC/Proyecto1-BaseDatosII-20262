import struct
import os

class BPlusTree:
    def __init__(self, filename='bplus_tree.idx', block_size=4096, io_counter=None):
        self.filename = filename
        self.block_size = block_size
        self.io_counter = io_counter 
        
        self.TREE_HEADER_FORMAT = '=i'
        self.TREE_HEADER_SIZE = struct.calcsize(self.TREE_HEADER_FORMAT)
        
        # [AGREGADO]: is_leaf, num_keys, next_leaf, prev_leaf (4 enteros = 16 bytes)
        self.NODE_HEADER_FORMAT = '=i i i i'
        self.NODE_HEADER_SIZE = struct.calcsize(self.NODE_HEADER_FORMAT)
        
        self.LEAF_ENTRY_SIZE = 12 # Key (4) + PageID (4) + SlotID (4)
        
        self.MAX_LEAF_KEYS = (self.block_size - self.NODE_HEADER_SIZE) // self.LEAF_ENTRY_SIZE
        self.MAX_INTERNAL_KEYS = (self.block_size - self.NODE_HEADER_SIZE - 4) // 8
        
        if not os.path.exists(self.filename):
            with open(self.filename, 'wb') as f:
                f.write(struct.pack(self.TREE_HEADER_FORMAT, -1))

    def _add_read(self):
        if self.io_counter: self.io_counter.disk_reads += 1

    def _add_write(self):
        if self.io_counter: self.io_counter.disk_writes += 1

    def _read_root(self):
        self._add_read()  
        with open(self.filename, 'rb') as f:
            f.seek(0)
            return struct.unpack(self.TREE_HEADER_FORMAT, f.read(self.TREE_HEADER_SIZE))[0]

    def _write_root(self, root_id):
        self._add_write() 
        with open(self.filename, 'r+b') as f:
            f.seek(0)
            f.write(struct.pack(self.TREE_HEADER_FORMAT, root_id))

    def _allocate_node(self, is_leaf):
        self._add_write()
        file_size = os.path.getsize(self.filename)
        new_id = (file_size - self.TREE_HEADER_SIZE) // self.block_size
        
        with open(self.filename, 'ab') as f:
            f.write(b'\x00' * self.block_size)
            
        # [AGREGADO]: prev_leaf inicializado en -1
        node = {'id': new_id, 'is_leaf': is_leaf, 'num_keys': 0, 'next_leaf': -1, 'prev_leaf': -1}
        if is_leaf:
            node['entries'] = [] 
        else:
            node['keys'] = []
            node['children'] = []
        return node

    def _read_node(self, block_id):
        if block_id == -1: return None
        self._add_read()
        
        offset = self.TREE_HEADER_SIZE + block_id * self.block_size
        with open(self.filename, 'rb') as f:
            f.seek(offset)
            block_data = f.read(self.block_size)
            
            is_leaf, num_keys, next_leaf, prev_leaf = struct.unpack(self.NODE_HEADER_FORMAT, block_data[:self.NODE_HEADER_SIZE])
            node = {'id': block_id, 'is_leaf': is_leaf, 'num_keys': num_keys, 'next_leaf': next_leaf, 'prev_leaf': prev_leaf}
            
            if is_leaf:
                entries = []
                data_offset = self.NODE_HEADER_SIZE
                for _ in range(num_keys):
                    k, pid, sid = struct.unpack('=iii', block_data[data_offset:data_offset+12])
                    entries.append({'key': k, 'rid': (pid, sid)})
                    data_offset += 12
                node['entries'] = entries
            else:
                keys = []
                key_offset = self.NODE_HEADER_SIZE
                for _ in range(num_keys):
                    keys.append(struct.unpack('=i', block_data[key_offset:key_offset+4])[0])
                    key_offset += 4
                    
                children = []
                child_offset = self.NODE_HEADER_SIZE + (self.MAX_INTERNAL_KEYS * 4)
                for _ in range(num_keys + 1):
                    children.append(struct.unpack('=i', block_data[child_offset:child_offset+4])[0])
                    child_offset += 4
                    
                node['keys'] = keys
                node['children'] = children
        return node

    def _write_node(self, node):
        self._add_write()
        offset = self.TREE_HEADER_SIZE + node['id'] * self.block_size
        
        with open(self.filename, 'r+b') as f:
            f.seek(offset)
            f.write(struct.pack(self.NODE_HEADER_FORMAT, node['is_leaf'], node['num_keys'], node['next_leaf'], node['prev_leaf']))
            
            if node['is_leaf']:
                for entry in node['entries']:
                    f.write(struct.pack('=iii', entry['key'], entry['rid'][0], entry['rid'][1]))
            else:
                for key in node['keys']:
                    f.write(struct.pack('=i', key))
                
                padding_keys = self.MAX_INTERNAL_KEYS - node['num_keys']
                f.write(b'\x00' * (4 * padding_keys))
                
                for child in node['children']:
                    f.write(struct.pack('=i', child))
                    
                padding_children = (self.MAX_INTERNAL_KEYS + 1) - (node['num_keys'] + 1)
                f.write(b'\x00' * (4 * padding_children))

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
            
            split_idx = node['num_keys'] // 2
            new_leaf = self._allocate_node(is_leaf=1)
            new_leaf['entries'] = entries[split_idx:]
            new_leaf['num_keys'] = len(new_leaf['entries'])
            
            node['entries'] = entries[:split_idx]
            node['num_keys'] = len(node['entries'])
            
            # [AGREGADO]: Lógica doblemente enlazada (next y prev)
            new_leaf['next_leaf'] = node['next_leaf']
            new_leaf['prev_leaf'] = node['id']
            
            if node['next_leaf'] != -1:
                next_node = self._read_node(node['next_leaf'])
                next_node['prev_leaf'] = new_leaf['id']
                self._write_node(next_node)
                
            node['next_leaf'] = new_leaf['id']
            
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
            
            if curr_node['next_leaf'] != -1:
                curr_node = self._read_node(curr_node['next_leaf'])
            else:
                curr_node = None
        return results

    # [AGREGADO]: Función de Eliminación adaptada a RIDs (Borrado físico de la hoja)
    def remove(self, key):
        root_id = self._read_root()
        if root_id == -1: return False
        
        curr_node = self._read_node(root_id)
        
        # 1. Bajar hasta la hoja correspondiente
        while not curr_node['is_leaf']:
            idx = len(curr_node['keys'])
            for i, k in enumerate(curr_node['keys']):
                if key < k:
                    idx = i
                    break
            curr_node = self._read_node(curr_node['children'][idx])
            
        # 2. Buscar y borrar la clave de la hoja
        entries = curr_node['entries']
        for i, entry in enumerate(entries):
            if entry['key'] == key:
                entries.pop(i)
                curr_node['num_keys'] -= 1
                self._write_node(curr_node)
                return True 
                
        return False