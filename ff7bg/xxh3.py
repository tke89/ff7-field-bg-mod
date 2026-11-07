"""XXH3_64bits (seed 0, default secret), ported from xxHash's reference xxhash.h: FFNx names animated textures
by this hash of the converted page. The long-input loop is vectorised with numpy: within a block, every stripe's
contribution to the accumulators is a plain sum. Checked against all 4161 seed-0 values in xxHash's
tests/sanity_test_vectors.h (lengths 0-4160, every code path)."""
import struct

import numpy as np

M = (1 << 64) - 1
P32_1, P32_2, P32_3 = 0x9E3779B1, 0x85EBCA77, 0xC2B2AE3D
P64_1, P64_2, P64_3, P64_4, P64_5 = (0x9E3779B185EBCA87, 0xC2B2AE3D27D4EB4F, 0x165667B19E3779F9,
                                    0x85EBCA77C2B2AE63, 0x27D4EB2F165667C5)
MX1, MX2 = 0x165667919E3779F9, 0x9FB21C651E98DF25
SECRET = bytes([
    0xb8, 0xfe, 0x6c, 0x39, 0x23, 0xa4, 0x4b, 0xbe, 0x7c, 0x01, 0x81, 0x2c, 0xf7, 0x21, 0xad, 0x1c,
    0xde, 0xd4, 0x6d, 0xe9, 0x83, 0x90, 0x97, 0xdb, 0x72, 0x40, 0xa4, 0xa4, 0xb7, 0xb3, 0x67, 0x1f,
    0xcb, 0x79, 0xe6, 0x4e, 0xcc, 0xc0, 0xe5, 0x78, 0x82, 0x5a, 0xd0, 0x7d, 0xcc, 0xff, 0x72, 0x21,
    0xb8, 0x08, 0x46, 0x74, 0xf7, 0x43, 0x24, 0x8e, 0xe0, 0x35, 0x90, 0xe6, 0x81, 0x3a, 0x26, 0x4c,
    0x3c, 0x28, 0x52, 0xbb, 0x91, 0xc3, 0x00, 0xcb, 0x88, 0xd0, 0x65, 0x8b, 0x1b, 0x53, 0x2e, 0xa3,
    0x71, 0x64, 0x48, 0x97, 0xa2, 0x0d, 0xf9, 0x4e, 0x38, 0x19, 0xef, 0x46, 0xa9, 0xde, 0xac, 0xd8,
    0xa8, 0xfa, 0x76, 0x3f, 0xe3, 0x9c, 0x34, 0x3f, 0xf9, 0xdc, 0xbb, 0xc7, 0xc7, 0x0b, 0x4f, 0x1d,
    0x8a, 0x51, 0xe0, 0x4b, 0xcd, 0xb4, 0x59, 0x31, 0xc8, 0x9f, 0x7e, 0xc9, 0xd9, 0x78, 0x73, 0x64,
    0xea, 0xc5, 0xac, 0x83, 0x34, 0xd3, 0xeb, 0xc3, 0xc5, 0x81, 0xa0, 0xff, 0xfa, 0x13, 0x63, 0xeb,
    0x17, 0x0d, 0xdd, 0x51, 0xb7, 0xf0, 0xda, 0x49, 0xd3, 0x16, 0x55, 0x26, 0x29, 0xd4, 0x68, 0x9e,
    0x2b, 0x16, 0xbe, 0x58, 0x7d, 0x47, 0xa1, 0xfc, 0x8f, 0xf8, 0xb8, 0xd1, 0x7a, 0xd0, 0x31, 0xce,
    0x45, 0xcb, 0x3a, 0x8f, 0x95, 0x16, 0x04, 0x28, 0xaf, 0xd7, 0xfb, 0xca, 0xbb, 0x4b, 0x40, 0x7e,
])


def _r64(b, o):
    return struct.unpack_from("<Q", b, o)[0]


def _r32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _fold(a, b):
    p = (a & M) * (b & M)
    return (p & M) ^ (p >> 64)


def _avalanche(h):
    h ^= h >> 37
    h = (h * MX1) & M
    return h ^ (h >> 32)


def _xxh64_avalanche(h):
    h ^= h >> 33
    h = (h * P64_2) & M
    h ^= h >> 29
    h = (h * P64_3) & M
    return h ^ (h >> 32)


def _rotl(x, r):
    return ((x << r) | (x >> (64 - r))) & M


def _rrmxmx(h, n):
    h ^= _rotl(h, 49) ^ _rotl(h, 24)
    h = (h * MX2) & M
    h ^= ((h >> 35) + n) & M
    h = (h * MX2) & M
    return h ^ (h >> 28)


def _mix16(d, o, so):
    return _fold(_r64(d, o) ^ _r64(SECRET, so), _r64(d, o + 8) ^ _r64(SECRET, so + 8))


def _swap64(x):
    return int.from_bytes(x.to_bytes(8, "little"), "big")


def _short(d):
    n = len(d)
    if n > 8:
        lo = _r64(d, 0) ^ (_r64(SECRET, 24) ^ _r64(SECRET, 32))
        hi = _r64(d, n - 8) ^ (_r64(SECRET, 40) ^ _r64(SECRET, 48))
        return _avalanche((n + _swap64(lo) + hi + _fold(lo, hi)) & M)
    if n >= 4:
        x = (_r32(d, n - 4) + (_r32(d, 0) << 32)) ^ (_r64(SECRET, 8) ^ _r64(SECRET, 16))
        return _rrmxmx(x, n)
    if n:
        c = (d[0] << 16) | (d[n >> 1] << 24) | d[n - 1] | (n << 8)
        return _xxh64_avalanche(c ^ (_r32(SECRET, 0) ^ _r32(SECRET, 4)))
    return _xxh64_avalanche(_r64(SECRET, 56) ^ _r64(SECRET, 64))


def _mid(d):
    n = len(d)
    acc = (n * P64_1) & M
    if n <= 128:
        pairs = [(0, 0, n - 16, 16)]
        if n > 32:
            pairs.append((16, 32, n - 32, 48))
        if n > 64:
            pairs.append((32, 64, n - 48, 80))
        if n > 96:
            pairs.append((48, 96, n - 64, 112))
        for a, sa, b, sb in pairs:
            acc = (acc + _mix16(d, a, sa) + _mix16(d, b, sb)) & M
        return _avalanche(acc)
    for i in range(8):
        acc = (acc + _mix16(d, 16 * i, 16 * i)) & M
    end = _mix16(d, n - 16, 136 - 17)
    acc = _avalanche(acc)
    for i in range(8, n // 16):
        end = (end + _mix16(d, 16 * i, 16 * (i - 8) + 3)) & M
    return _avalanche((acc + end) & M)


def _lanes(b, o, count):
    return np.frombuffer(b, dtype="<u8", count=count, offset=o).astype(np.uint64)


SWAP = np.array([1, 0, 3, 2, 5, 4, 7, 6])


def _long(d):
    n = len(d)
    with np.errstate(over="ignore"):
        acc = np.array([P32_3, P64_1, P64_2, P64_3, P64_4, P32_2, P64_5, P32_1], dtype=np.uint64)
        per_block, block = 16, 1024  # (192 - 64) / 8 stripes, 64 bytes each
        keys = np.stack([_lanes(SECRET, 8 * s, 8) for s in range(per_block)])
        scramble = _lanes(SECRET, 192 - 64, 8)
        nb = (n - 1) // block
        data = np.frombuffer(d, dtype="<u8", count=(nb * block) // 8).astype(np.uint64).reshape(-1, per_block, 8) if nb else None

        def add(acc, stripes, k):
            dk = stripes ^ k
            prod = (dk & np.uint64(0xFFFFFFFF)) * (dk >> np.uint64(32))
            return acc + prod.sum(axis=0, dtype=np.uint64) + stripes.sum(axis=0, dtype=np.uint64)[SWAP]

        for b in range(nb):
            acc = add(acc, data[b], keys)
            acc = acc ^ (acc >> np.uint64(47))
            acc = acc ^ scramble
            acc = acc * np.uint64(P32_1)
        rest = ((n - 1) - block * nb) // 64
        if rest:
            tail = np.frombuffer(d, dtype="<u8", count=rest * 8, offset=nb * block).astype(np.uint64).reshape(rest, 8)
            acc = add(acc, tail, keys[:rest])
        last = np.frombuffer(d, dtype="<u8", count=8, offset=n - 64).astype(np.uint64).reshape(1, 8)
        acc = add(acc, last, _lanes(SECRET, 192 - 64 - 7, 8).reshape(1, 8))
    a = [int(x) for x in acc]
    res = (n * P64_1) & M
    for i in range(4):
        res = (res + _fold(a[2 * i] ^ _r64(SECRET, 11 + 16 * i), a[2 * i + 1] ^ _r64(SECRET, 11 + 16 * i + 8))) & M
    return _avalanche(res)


def xxh3_64(data):
    d = bytes(data)
    if len(d) <= 16:
        return _short(d)
    if len(d) <= 240:
        return _mid(d)
    return _long(d)
