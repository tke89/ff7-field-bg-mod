"""FF8 Remastered's archives: main.zzz -> data/field.{fs,fi,fl} -> each field's own fs/fi/fl -> .mim, .map, .jsm...

main.zzz is a plain index (count, then name length / name / offset / size per entry) over stored files. An fs/fi/fl
triple is the original PC format: fl lists the names, fi has (size, offset, compression) per name, fs the data. In
the Remaster every field file is LZ4 (compression 2, a "4ZL_" header); LZS (1) is read too.
"""
import struct
import threading
from pathlib import Path

from ff7bg import lzs

DEFAULT_GAME = Path(r"C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY VIII Remastered")


class Zzz:
    def __init__(self, path):
        self.path = Path(path)
        self.entries = {}
        with open(self.path, "rb") as f:
            (n,) = struct.unpack("<I", f.read(4))
            for _ in range(n):
                (length,) = struct.unpack("<I", f.read(4))
                name = f.read(length).decode("ascii", "replace").replace("\\", "/").lower()
                off, size = struct.unpack("<QI", f.read(12))
                self.entries[name] = (off, size)

    def read(self, name):
        off, size = self.entries[name.lower()]
        with open(self.path, "rb") as f:
            f.seek(off)
            return f.read(size)


def lz4_block(src, out_size=0):
    """Plain LZ4 block decompression."""
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        tok = src[i]
        i += 1
        lit = tok >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i : i + lit]
        i += lit
        if i >= n:
            break
        off = src[i] | (src[i + 1] << 8)
        i += 2
        ml = tok & 15
        if ml == 15:
            while True:
                b = src[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        start = len(out) - off
        if off >= ml:
            out += out[start : start + ml]
        else:  # overlapping copy: byte by byte
            for k in range(ml):
                out.append(out[start + k])
    return bytes(out[:out_size]) if out_size else bytes(out)


class Fs:
    """An FF8 fs/fi/fl triple held in memory."""

    def __init__(self, fs, fi, fl):
        self.fs = fs
        names = [line.strip().replace("\\", "/") for line in fl.decode("latin-1").splitlines() if line.strip()]
        self.entries = {}
        for k, name in enumerate(names):
            size, off, comp = struct.unpack_from("<III", fi, 12 * k)
            self.entries[name.lower()] = (size, off, comp)
        self.names = names

    def find(self, suffix):
        suffix = suffix.lower()
        return [n for n in self.entries if n.endswith(suffix)]

    def read(self, name):
        size, off, comp = self.entries[name.lower()]
        if comp == 0:
            return self.fs[off : off + size]
        if comp == 1:
            (clen,) = struct.unpack_from("<I", self.fs, off)
            return lzs.decompress(self.fs[off : off + 4 + clen])[:size]
        if comp == 2:
            (clen,) = struct.unpack_from("<I", self.fs, off)
            blob = self.fs[off + 4 : off + 4 + clen]
            return lz4_block(blob[8:], size) if blob[:4] == b"4ZL_" else lz4_block(blob, size)
        raise ValueError(f"compression {comp} in {name}")

    def sub(self, base):
        """A nested archive: base is the path without extension."""
        return Fs(self.read(base + ".fs"), self.read(base + ".fi"), self.read(base + ".fl"))


class FieldArchive:
    """The game's fields: names and each one's own archive (thread-safe; the field index is read once)."""

    def __init__(self, game_dir=DEFAULT_GAME):
        self.game_dir = Path(game_dir)
        self.zzz_path = self.game_dir / "main.zzz"
        self._root = None
        self._lock = threading.Lock()

    def root(self):
        with self._lock:
            if self._root is None:
                z = Zzz(self.zzz_path)
                self._root = Fs(z.read("data/field.fs"), z.read("data/field.fi"), z.read("data/field.fl"))
            return self._root

    def names(self):
        r = self.root()
        return sorted({n.rsplit("/", 1)[1][:-3] for n in r.entries if n.endswith(".fs")})

    def field(self, name):
        r = self.root()
        bases = [n for n in r.entries if n.endswith("/" + name.lower() + ".fs")]
        if not bases:
            raise KeyError(name)
        with self._lock:
            return r.sub(bases[0][:-3])

    def stamp(self):
        """Changes when the game's data does (a patch): size and time of main.zzz."""
        st = self.zzz_path.stat()
        return f"{st.st_size}:{st.st_mtime_ns}"
