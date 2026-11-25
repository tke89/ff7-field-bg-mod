"""Jump to a field by save file: a copy of one of the player's saves, standing in the chosen field.

FF8 Remastered saves (Documents/My Games/FINAL FANTASY VIII Remastered/Steam/<id>/game_data/user/saves/
slot<1|2>_save<NN>.ff8) are a 4-byte length plus LZS of the PlayStation memory-card block (Hyne, myst6re's save
editor: src/SaveData.h). In it (checked on the user's own saves, 2026-11-21):
- 384 and 5408: a 16-bit checksum (the same value twice), 386: 0xFF08, 388: the place-name number the load screen
  shows (SETPLACE / areames.dc1), 464-5408: the game's data.
- 3776 module (1 = field), 3778 current field (maplist index), 3780 previous field, 3782 x[3], 3788 y[3] (int16,
  walkmesh coordinates), 3794 walkmesh triangle[3] (uint16), 3800 facing[3] (one byte each), for the three party
  members. Each saved (x, y) lies inside its saved triangle.

The game checks the checksum ("Saved data is damaged" otherwise): CRC-16/CCITT (polynomial 0x1021, start 0xFFFF,
complemented) over the 4944 bytes at 464, with the table's last entry 0 instead of 0x1EF0, as Hyne has it
(SaveData::crcTab); with the standard table it didn't match. It matches all 32 of the user's saves."""
import struct
from pathlib import Path

from ff7bg import lzs

SAVES = Path.home() / "Documents" / "My Games" / "FINAL FANTASY VIII Remastered" / "Steam"
JUMP_SLOT, JUMP_NUM = 2, 30  # the save the Studio writes: slot 2, save 30 (the last entry, kept for this)
PLACE, MODULE, FIELD, LAST_FIELD, X, Y, TRI, DIR = 388, 3776, 3778, 3780, 3782, 3788, 3794, 3800
MODULE_FIELD = 1
MAIN, MAIN_SIZE, CHECKSUMS = 464, 4944, (384, 5408)


def _crc_table():
    tab = []
    for i in range(256):
        c = i << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
        tab.append(c)
    tab[255] = 0  # the game's (and Hyne's) table has 0 here
    return tab


CRC_TABLE = _crc_table()


def checksum(block):
    c = 0xFFFF
    for b in block[MAIN : MAIN + MAIN_SIZE]:
        c = (CRC_TABLE[(c >> 8) ^ b] ^ (c << 8)) & 0xFFFF
    return ~c & 0xFFFF


def seal(block):
    """The block with its checksum written in both places."""
    d = bytearray(block)
    for at in CHECKSUMS:
        struct.pack_into("<H", d, at, checksum(d))
    return bytes(d)


def saves_dir():
    """The first Steam user's saves folder."""
    for d in sorted(SAVES.glob("*/game_data/user/saves")):
        return d
    raise FileNotFoundError(f"no FF8 Remastered saves under {SAVES}")


def read(path):
    """A save file's memory-card block (bytes)."""
    return lzs.decompress(Path(path).read_bytes())


def compress(block):
    """LZS with no back-references (every byte a literal): what the game's LZS reader takes, a little bigger."""
    out = bytearray()
    for i in range(0, len(block), 8):
        chunk = block[i : i + 8]
        out.append((1 << len(chunk)) - 1)  # a flag bit per byte, 1 = literal
        out += chunk
    return struct.pack("<I", len(out)) + bytes(out)


def field_id(maplist, name):
    return maplist.index(name)


def walkmesh(id_data):
    """The .id walkmesh: [((x, y, z) * 3)] triangles (u32 count, then 24 bytes each: three int16 x, y, z, pad)."""
    (n,) = struct.unpack_from("<I", id_data, 0)
    tris = []
    for i in range(n):
        v = struct.unpack_from("<12h", id_data, 4 + 24 * i)
        tris.append(((v[0], v[1], v[2]), (v[4], v[5], v[6]), (v[8], v[9], v[10])))
    return tris


def standing_spot(tris):
    """(x, y, triangle) to stand on: the centre of the triangle nearest the middle of the walkable area."""
    cents = [((a[0] + b[0] + c[0]) / 3, (a[1] + b[1] + c[1]) / 3) for a, b, c in tris]
    mx = sorted(x for x, _ in cents)[len(cents) // 2]
    my = sorted(y for _, y in cents)[len(cents) // 2]
    i = min(range(len(cents)), key=lambda k: (cents[k][0] - mx) ** 2 + (cents[k][1] - my) ** 2)
    return round(cents[i][0]), round(cents[i][1]), i


def jump_block(base, fid, x, y, tri, place=None):
    """A copy of a save block standing in field fid at (x, y) on walkmesh triangle tri, all three members there."""
    d = bytearray(base)
    struct.pack_into("<H", d, MODULE, MODULE_FIELD)
    last = struct.unpack_from("<H", d, FIELD)[0]
    struct.pack_into("<HH", d, FIELD, fid, last)
    struct.pack_into("<3h", d, X, x, x, x)
    struct.pack_into("<3h", d, Y, y, y, y)
    struct.pack_into("<3H", d, TRI, tri, tri, tri)
    d[DIR : DIR + 3] = b"\x00\x00\x00"
    if place is not None:
        struct.pack_into("<H", d, PLACE, place)
    return seal(d)


def jump_path(folder=None, slot=JUMP_SLOT, num=JUMP_NUM):
    return Path(folder or saves_dir()) / f"slot{slot}_save{num:02d}.ff8"
