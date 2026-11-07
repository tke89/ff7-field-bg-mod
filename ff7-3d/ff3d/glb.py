"""Minimal GLB reader: positions, uvs, indices of the first mesh primitive."""
import json, struct
import numpy as np

COMP = {5126: np.float32, 5125: np.uint32, 5123: np.uint16, 5121: np.uint8}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def read(path):
    b = open(path, "rb").read()
    magic, ver, length = struct.unpack_from("<4sII", b, 0)
    assert magic == b"glTF"
    off, js, binc = 12, None, None
    while off < length:
        clen, ctype = struct.unpack_from("<I4s", b, off)
        data = b[off + 8: off + 8 + clen]
        if ctype == b"JSON":
            js = json.loads(data)
        elif ctype[:3] == b"BIN":
            binc = data
        off += 8 + clen

    def acc(i):
        a = js["accessors"][i]
        bv = js["bufferViews"][a["bufferView"]]
        start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        n = NCOMP[a["type"]]
        arr = np.frombuffer(binc, dtype=COMP[a["componentType"]], count=a["count"] * n, offset=start)
        return arr.reshape(a["count"], n) if n > 1 else arr

    prim = js["meshes"][0]["primitives"][0]
    out = {"json": js, "pos": acc(prim["attributes"]["POSITION"]), "idx": acc(prim["indices"]) if "indices" in prim else None}
    if "TEXCOORD_0" in prim["attributes"]:
        out["uv"] = acc(prim["attributes"]["TEXCOORD_0"])
    return out
