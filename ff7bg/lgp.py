"""Read-only access to Square's LGP archives (flevel.lgp, char.lgp, ...)."""
import struct
from dataclasses import dataclass


@dataclass
class LgpEntry:
    name: str
    offset: int


class LgpArchive:
    def __init__(self, path):
        self.path = path
        self._f = open(path, "rb")
        header = self._f.read(16)
        if header[2:12] != b"SQUARESOFT":
            raise ValueError(f"{path} is not an LGP archive")
        (count,) = struct.unpack_from("<I", header, 12)
        toc = self._f.read(count * 27)
        self.entries = {}
        for i in range(count):
            raw = toc[i * 27 : i * 27 + 27]
            name = raw[:20].split(b"\0", 1)[0].decode("ascii")
            (offset,) = struct.unpack_from("<I", raw, 20)
            self.entries[name.lower()] = LgpEntry(name, offset)

    def names(self):
        return [e.name for e in self.entries.values()]

    def read(self, name):
        entry = self.entries[name.lower()]
        self._f.seek(entry.offset)
        header = self._f.read(24)
        (size,) = struct.unpack_from("<I", header, 20)
        return self._f.read(size)

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
