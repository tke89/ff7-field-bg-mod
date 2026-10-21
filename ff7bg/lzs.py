"""FF7's LZS (Okumura LZSS variant) decompressor."""
import struct


def decompress(data):
    """Decompress an LZS blob that starts with its 4-byte compressed length."""
    (length,) = struct.unpack_from("<I", data, 0)
    src = memoryview(data)[4 : 4 + length]
    out = bytearray()
    ring = bytearray(4096)
    r = 0xFEE
    i = 0
    n = len(src)
    while i < n:
        flags = src[i]
        i += 1
        for bit in range(8):
            if i >= n:
                break
            if flags & (1 << bit):
                b = src[i]
                i += 1
                out.append(b)
                ring[r] = b
                r = (r + 1) & 0xFFF
            else:
                if i + 1 >= n:
                    i = n
                    break
                lo, hi = src[i], src[i + 1]
                i += 2
                offset = lo | ((hi & 0xF0) << 4)
                count = (hi & 0x0F) + 3
                for k in range(count):
                    b = ring[(offset + k) & 0xFFF]
                    out.append(b)
                    ring[r] = b
                    r = (r + 1) & 0xFFF
    return bytes(out)
