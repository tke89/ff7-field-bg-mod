"""Read 7th Heaven .iro archives (version 0x10002): directory of (name, flags, offset, length)."""
import struct


def entries(path):
    with open(path, "rb") as f:
        magic, version, flags, diroff, count = struct.unpack("<4sIIII", f.read(20))
        assert magic == b"IROS", magic
        out = []
        for _ in range(count):
            size, nlen = struct.unpack("<HH", f.read(4))
            name = f.read(nlen).decode("utf-16-le")
            rest = f.read(size - 4 - nlen)
            fflags, offset, length = struct.unpack_from("<IqI", rest)
            out.append((name, fflags, offset, length))
        return version, flags, out


def read(path, entry):
    name, fflags, offset, length = entry
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(length)
