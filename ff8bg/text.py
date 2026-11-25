"""FF8's English text (field and menu strings), and the place names the save screen shows (menu/areames.dc1).

Control codes (Deling, src/FF8Text.cpp): 0x00 ends a string, 0x01 a new page, 0x02 a new line, 0x03 a character's
name (one byte follows), 0x04 a variable, 0x06 a colour, 0x09 a wait, 0x0E a place word (namedic.bin: 0x20 Galbadia
... 0x3D Ultimecia Castle, 0x3E Garden), and every other code below 0x20 takes one byte. From 0x20 the English
font's characters, then two-letter pairs at 0xE8-0xFF. The table was checked against the game's own place names
(all 251 in areames.dc1 read as English; 246 is "Ultimecia Castle- Master Room" as the save screen shows it).
"""
import struct

_CHARS = (
    " 0123456789%/:!?…+-=*&「」()·.,~“”‘#$'_"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
)
TABLE = {0x20 + i: c for i, c in enumerate(_CHARS)}
PAIRS = ["in", "e ", "ne", "to", "re", "HP", "l ", "ll", "GF", "nt", "il", "o ", "ef", "on", " w", " r", "wi", "fi",
         "EC", "s ", "ar", "FE", " S", "ag"]
TABLE.update({0xE8 + i: p for i, p in enumerate(PAIRS)})
# 0x0E's words (namedic.bin in lang-en/main: Galbadia, Esthar... Ultimecia Castle, Garden), read by place_names()
LOCATIONS = ["Galbadia", "Esthar", "Balamb", "Dollet", "Timber", "Trabia", "Centra", "Fishermans Horizon"]


def string_table(data):
    """A u16 count, then that many u16 offsets (from the file's start) of 0-ended strings (areames.dc1,
    namedic.bin)."""
    (n,) = struct.unpack_from("<H", data, 0)
    return struct.unpack_from(f"<{n}H", data, 2)


def decode(data, start=0, words=None):
    """One string from start up to its 0x00 (words: the 0x0E place words, default LOCATIONS)."""
    words = words or LOCATIONS
    out = []
    i = start
    while i < len(data):
        b = data[i]
        i += 1
        if b == 0x00:
            break
        if b in (0x01, 0x02):
            out.append(" ")
        elif b == 0x0E and i < len(data):
            k = data[i] - 0x20
            out.append(words[k] if 0 <= k < len(words) else "")
            i += 1
        elif b < 0x20:
            i += 1  # names, variables, colours, waits: one byte each
        else:
            out.append(TABLE.get(b, ""))
    return "".join(out).strip()


def area_names(data, words=None):
    """menu/areames.dc1: the save screen's place names, by SETPLACE number."""
    return [decode(data, o, words) for o in string_table(data)]


def place_names(zzz):
    """The game's place names (SETPLACE number -> "B-Garden- Classroom"), from main.zzz's English menu and main
    archives."""
    from .archive import Fs

    def archive(base):
        return Fs(zzz.read(base + ".fs"), zzz.read(base + ".fi"), zzz.read(base + ".fl"))

    main = archive("data/lang-en/main")
    dic = main.read(main.find("/namedic.bin")[0])
    words = [decode(dic, o) for o in string_table(dic)]
    menu = archive("data/lang-en/menu")
    return area_names(menu.read(menu.find("/areames.dc1")[0]), words)
