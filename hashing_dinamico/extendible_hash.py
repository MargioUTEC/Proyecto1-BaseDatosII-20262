from __future__ import annotations

import struct
from typing import List, Optional, Tuple

from storage import StorageBackend

RID = Tuple[int, int]  #aqui guardamos pagina y slot, o sea donde esta el registro real

#formato del header del indice: profundidad global, primera pagina del directorio, primera pagina libre, cuantos registros hay
HEADER_FMT = "<Iiii"
HEADER_SIZE = struct.calcsize(HEADER_FMT)

#formato del header de un bucket: profundidad local, cuantas entradas tiene, siguiente pagina de overflow
BUCKET_HEADER_FMT = "<iIi"
BUCKET_HEADER_SIZE = struct.calcsize(BUCKET_HEADER_FMT)

#cada entrada guarda la clave y el rid (pagina + slot)
ENTRY_FMT = "<qII"
ENTRY_SIZE = struct.calcsize(ENTRY_FMT)

#formato del header de una pagina de directorio: apunta a la siguiente pagina de directorio
DIR_HEADER_FMT = "<i"
DIR_HEADER_SIZE = struct.calcsize(DIR_HEADER_FMT)

FREE_LIST_LINK_FMT = "<i"  #los primeros 4 bytes de una pagina libre guardan cual es la siguiente libre


class ExtendibleHashFile:
    #hasta aqui dejamos crecer la profundidad local antes de usar overflow como ultimo recurso
    MAX_LOCAL_DEPTH = 20

    def __init__(
        self,
        storage: StorageBackend,
        header_page_id: int,
        global_depth: int,
        first_dir_page_id: int,
        free_page_head: int,
        total_entries: int,
        directory: List[int],
    ):
        self.storage = storage
        self.header_page_id = header_page_id
        self.global_depth = global_depth
        self.first_dir_page_id = first_dir_page_id
        self.free_page_head = free_page_head
        self.total_entries = total_entries
        self._directory = directory  #esto vive en memoria mientras el indice esta abierto

    @classmethod
    def create(cls, storage: StorageBackend) -> "ExtendibleHashFile":
        #crea el indice desde cero, arranca con un solo bucket
        header_page_id = storage.allocate_page()
        obj = cls(
            storage=storage,
            header_page_id=header_page_id,
            global_depth=0,
            first_dir_page_id=-1,
            free_page_head=-1,
            total_entries=0,
            directory=[],
        )
        bucket0 = obj._alloc_page()
        obj._init_bucket(bucket0, local_depth=0)
        obj._directory = [bucket0]
        obj.first_dir_page_id = obj._write_directory_to_disk(obj._directory)
        obj._flush_header()
        return obj

    @classmethod
    def open(cls, storage: StorageBackend, header_page_id: int) -> "ExtendibleHashFile":
        #abre un indice que ya existia y carga el directorio a memoria
        raw = storage.read_page(header_page_id)
        global_depth, first_dir_page_id, free_page_head, total_entries = struct.unpack_from(
            HEADER_FMT, raw, 0
        )
        obj = cls(
            storage=storage,
            header_page_id=header_page_id,
            global_depth=global_depth,
            first_dir_page_id=first_dir_page_id,
            free_page_head=free_page_head,
            total_entries=total_entries,
            directory=[],
        )
        obj._directory = obj._read_directory_from_disk(first_dir_page_id, global_depth)
        return obj

    #de aqui para abajo son los metodos que se usan desde afuera

    def insert(self, key: int, rid: RID) -> None:
        self._insert_key(key, rid)
        self.total_entries += 1
        self._flush_header()

    def search(self, key: int) -> List[RID]:
        page_id = self._directory[self._bucket_index(key, self.global_depth)]
        _, overflow_pid, entries = self._read_bucket(page_id)
        results = [rid for k, rid in entries if k == key]
        pid = overflow_pid
        while pid != -1:
            _, next_pid, ov_entries = self._read_bucket(pid)
            results.extend(rid for k, rid in ov_entries if k == key)
            pid = next_pid
        return results

    def delete(self, key: int, rid: Optional[RID] = None) -> bool:
        #si no le pasas el rid, borra la primera que encuentre con esa clave
        page_id = self._directory[self._bucket_index(key, self.global_depth)]
        local_depth, overflow_pid, entries = self._read_bucket(page_id)

        removed = False
        for i, (k, r) in enumerate(entries):
            if k == key and (rid is None or r == rid):
                del entries[i]
                removed = True
                break

        if removed:
            self._write_bucket(page_id, local_depth, entries, overflow_pid)
        elif overflow_pid != -1:
            removed = self._delete_from_overflow_chain(overflow_pid, key, rid)

        if removed:
            self.total_entries = max(0, self.total_entries - 1)
            self._flush_header()
            self._maybe_merge(page_id, local_depth)
        return removed

    def stats(self) -> dict:
        metrics = self.storage.counter.get_metrics()
        return {
            "global_depth": self.global_depth,
            "directory_entries": len(self._directory),
            "distinct_buckets": len(set(self._directory)),
            "total_entries": self.total_entries,
            "bucket_capacity": self._bucket_capacity(),
            "block_size": self.storage.page_size,
            **metrics,
        }

    #de aqui para abajo es como se inserta y como se divide un bucket cuando se llena

    def _insert_key(self, key: int, rid: RID) -> None:
        page_id = self._directory[self._bucket_index(key, self.global_depth)]
        local_depth, overflow_pid, entries = self._read_bucket(page_id)
        cap = self._bucket_capacity()

        if len(entries) < cap:
            entries.append((key, rid))
            self._write_bucket(page_id, local_depth, entries, overflow_pid)
            return

        if local_depth >= self.MAX_LOCAL_DEPTH:
            #ya no se puede dividir mas, se manda a la cadena de overflow
            self._insert_into_overflow_chain(page_id, key, rid)
            return

        self._split_bucket(page_id, local_depth, entries, overflow_pid)
        #reintenta despues de dividir, esto siempre termina porque la profundidad va subiendo
        self._insert_key(key, rid)

    def _split_bucket(
        self, page_id: int, local_depth: int, entries: List[Tuple[int, RID]], overflow_pid: int
    ) -> None:
        if overflow_pid != -1:
            #esto casi no deberia pasar, pero por si acaso rescatamos lo que haya en overflow
            entries = entries + self._drain_overflow_chain(overflow_pid)

        if local_depth == self.global_depth:
            self._grow_directory()

        new_local_depth = local_depth + 1
        bit_pos = local_depth  #este bit decide si la entrada se queda o se va al bucket nuevo
        sibling_page_id = self._alloc_page()

        kept: List[Tuple[int, RID]] = []
        moved: List[Tuple[int, RID]] = []
        for key, rid in entries:
            (moved if (key >> bit_pos) & 1 else kept).append((key, rid))

        self._write_bucket(page_id, new_local_depth, kept)
        self._write_bucket(sibling_page_id, new_local_depth, moved)
        self._update_directory_after_split(page_id, sibling_page_id, local_depth)

    def _grow_directory(self) -> None:
        #duplicamos el directorio, cada mitad nueva es copia exacta de la mitad vieja
        self.global_depth += 1
        self._directory = self._directory + self._directory
        self.first_dir_page_id = self._write_directory_to_disk(self._directory)

    def _update_directory_after_split(self, page_id: int, sibling_page_id: int, old_local_depth: int) -> None:
        mask_old = (1 << old_local_depth) - 1
        new_bit = 1 << old_local_depth

        #cualquier casilla que hoy apunte a page_id comparte el mismo sufijo
        r = next(i & mask_old for i, p in enumerate(self._directory) if p == page_id)

        for i in range(len(self._directory)):
            if (i & mask_old) == r:
                self._directory[i] = sibling_page_id if (i & new_bit) else page_id

        self.first_dir_page_id = self._write_directory_to_disk(self._directory)

    #de aqui para abajo es como se borra y se juntan buckets vacios

    def _maybe_merge(self, page_id: int, local_depth: int) -> None:
        #solo junta buckets cuando uno queda vacio y su pareja tiene la misma profundidad
        if local_depth == 0:
            return
        local_depth, overflow_pid, entries = self._read_bucket(page_id)
        if entries or overflow_pid != -1:
            return

        mask = (1 << (local_depth - 1)) - 1
        buddy_bit = 1 << (local_depth - 1)
        my_index = next(i for i, p in enumerate(self._directory) if p == page_id)
        r = my_index & mask
        my_bit = my_index & buddy_bit

        buddy_index = next(
            (
                i
                for i in range(len(self._directory))
                if (i & mask) == r and (i & buddy_bit) != my_bit
            ),
            None,
        )
        if buddy_index is None:
            return

        buddy_page_id = self._directory[buddy_index]
        buddy_local_depth, buddy_overflow, buddy_entries = self._read_bucket(buddy_page_id)
        if buddy_local_depth != local_depth:
            return  #solo se juntan si estan al mismo nivel

        for i in range(len(self._directory)):
            if self._directory[i] == page_id:
                self._directory[i] = buddy_page_id
        self._write_bucket(buddy_page_id, local_depth - 1, buddy_entries, buddy_overflow)
        self.first_dir_page_id = self._write_directory_to_disk(self._directory)
        self._free_page(page_id)

    #de aqui para abajo es la cadena de overflow, el ultimo recurso

    def _insert_into_overflow_chain(self, bucket_page_id: int, key: int, rid: RID) -> None:
        local_depth, first_overflow, _entries_unused = self._read_bucket(bucket_page_id)
        cap = self._bucket_capacity()
        pid = first_overflow
        while pid != -1:
            _, next_pid, ov_entries = self._read_bucket(pid)
            if len(ov_entries) < cap:
                ov_entries.append((key, rid))
                self._write_bucket(pid, -1, ov_entries, next_pid)
                return
            pid = next_pid

        new_pid = self._alloc_page()
        self._write_bucket(new_pid, -1, [(key, rid)], first_overflow)
        _, _, bucket_entries = self._read_bucket(bucket_page_id)
        self._write_bucket(bucket_page_id, local_depth, bucket_entries, new_pid)

    def _drain_overflow_chain(self, first_pid: int) -> List[Tuple[int, RID]]:
        collected: List[Tuple[int, RID]] = []
        pid = first_pid
        while pid != -1:
            _, next_pid, ov_entries = self._read_bucket(pid)
            collected.extend(ov_entries)
            self._free_page(pid)
            pid = next_pid
        return collected

    def _delete_from_overflow_chain(self, first_pid: int, key: int, rid: Optional[RID]) -> bool:
        pid = first_pid
        while pid != -1:
            _, next_pid, ov_entries = self._read_bucket(pid)
            for i, (k, r) in enumerate(ov_entries):
                if k == key and (rid is None or r == rid):
                    del ov_entries[i]
                    self._write_bucket(pid, -1, ov_entries, next_pid)
                    return True
            pid = next_pid
        return False

    def _bucket_index(self, key: int, depth: int) -> int:
        #esto es lo que vimos en clase: los bits mas bajos de la clave, key % 2^depth
        if depth == 0:
            return 0
        return key & ((1 << depth) - 1)

    def _bucket_capacity(self) -> int:
        return (self.storage.page_size - BUCKET_HEADER_SIZE) // ENTRY_SIZE

    def _init_bucket(self, page_id: int, local_depth: int) -> None:
        self._write_bucket(page_id, local_depth, [])

    def _read_bucket(self, page_id: int) -> Tuple[int, int, List[Tuple[int, RID]]]:
        raw = self.storage.read_page(page_id)
        local_depth, num_entries, next_page_id = struct.unpack_from(BUCKET_HEADER_FMT, raw, 0)
        entries: List[Tuple[int, RID]] = []
        offset = BUCKET_HEADER_SIZE
        for _ in range(num_entries):
            key, rid_page, rid_slot = struct.unpack_from(ENTRY_FMT, raw, offset)
            entries.append((key, (rid_page, rid_slot)))
            offset += ENTRY_SIZE
        return local_depth, next_page_id, entries

    def _write_bucket(
        self, page_id: int, local_depth: int, entries: List[Tuple[int, RID]], overflow_pid: int = -1
    ) -> None:
        cap = self._bucket_capacity()
        if len(entries) > cap:
            raise ValueError(
                f"El bucket {page_id} tiene {len(entries)} entradas, supera la "
                f"capacidad de {cap} para block_size={self.storage.page_size}."
            )
        buf = bytearray(self.storage.page_size)
        struct.pack_into(BUCKET_HEADER_FMT, buf, 0, local_depth, len(entries), overflow_pid)
        offset = BUCKET_HEADER_SIZE
        for key, (rp, rs) in entries:
            struct.pack_into(ENTRY_FMT, buf, offset, key, rp, rs)
            offset += ENTRY_SIZE
        self.storage.write_page(page_id, bytes(buf))

    def _entries_per_dir_page(self) -> int:
        return (self.storage.page_size - DIR_HEADER_SIZE) // 4

    def _directory_page_chain(self, first_page_id: int) -> List[int]:
        pages = []
        pid = first_page_id
        while pid != -1:
            pages.append(pid)
            raw = self.storage.read_page(pid)
            (next_pid,) = struct.unpack_from(DIR_HEADER_FMT, raw, 0)
            pid = next_pid
        return pages

    def _read_directory_from_disk(self, first_page_id: int, global_depth: int) -> List[int]:
        entries: List[int] = []
        per_page = self._entries_per_dir_page()
        pid = first_page_id
        while pid != -1:
            raw = self.storage.read_page(pid)
            (next_pid,) = struct.unpack_from(DIR_HEADER_FMT, raw, 0)
            offset = DIR_HEADER_SIZE
            for _ in range(per_page):
                (val,) = struct.unpack_from("<I", raw, offset)
                entries.append(val)
                offset += 4
            pid = next_pid
        return entries[: 1 << global_depth]

    def _write_directory_to_disk(self, entries: List[int]) -> int:
        per_page = self._entries_per_dir_page()
        pages_needed = max(1, -(-len(entries) // per_page))

        old_pages = (
            self._directory_page_chain(self.first_dir_page_id) if self.first_dir_page_id != -1 else []
        )
        pages = list(old_pages[:pages_needed])
        while len(pages) < pages_needed:
            pages.append(self._alloc_page())
        for extra in old_pages[pages_needed:]:
            self._free_page(extra)

        idx = 0
        for i, pid in enumerate(pages):
            next_pid = pages[i + 1] if i + 1 < len(pages) else -1
            chunk = entries[idx : idx + per_page]
            idx += per_page
            buf = bytearray(self.storage.page_size)
            struct.pack_into(DIR_HEADER_FMT, buf, 0, next_pid)
            offset = DIR_HEADER_SIZE
            for value in chunk:
                struct.pack_into("<I", buf, offset, value)
                offset += 4
            self.storage.write_page(pid, bytes(buf))

        return pages[0]

    #de aqui para abajo es como reutilizamos paginas que quedaron libres

    def _alloc_page(self) -> int:
        if self.free_page_head != -1:
            page_id = self.free_page_head
            raw = self.storage.read_page(page_id)
            (next_free,) = struct.unpack_from(FREE_LIST_LINK_FMT, raw, 0)
            self.free_page_head = next_free
            return page_id
        return self.storage.allocate_page()

    def _free_page(self, page_id: int) -> None:
        buf = bytearray(self.storage.page_size)
        struct.pack_into(FREE_LIST_LINK_FMT, buf, 0, self.free_page_head)
        self.storage.write_page(page_id, bytes(buf))
        self.free_page_head = page_id

    def _flush_header(self) -> None:
        buf = bytearray(self.storage.page_size)
        struct.pack_into(
            HEADER_FMT, buf, 0, self.global_depth, self.first_dir_page_id, self.free_page_head, self.total_entries
        )
        self.storage.write_page(self.header_page_id, bytes(buf))
