"""glTF 2.0 (.gltf + .bin) and DDS files the way FFNx's external meshes read them (its external_mesh.cpp, through
cgltf), for field model parts (field.export_part) and anything else drawn as an external mesh.

What FFNx's importExternalMeshGltfFile reads, and so what this writes:
- Every accessor is read from its bufferView's start, tightly packed (accessor offsets and strides are ignored): one
  bufferView per accessor read, no strides. Animation times aren't read, so accessors of equal times are shared.
- Per primitive: POSITION (float, with min and max), NORMAL (float: required, it's read unchecked), TEXCOORD_0
  (float), JOINTS_0 (read as unsigned bytes) and WEIGHTS_0 (float); indices (u16 or u32); a material (required).
  COLOR_0 is read but never used: every vertex takes the material's baseColorFactor as its colour, which multiplies
  the texture (FFNx.lighting.frag: color = v_color0 x texture), so the colour lives in the texture.
- Textures: an image's uri names it (its file name without extension); FFNx loads <tex_path>/<name>.dds (field:
  <external_mesh_path>/field/textures/), as sRGB, unless <mod_path>/world/<name>_00.dds exists. A primitive's
  material finds its texture by baseColorTexture's image *name*, so each image's name must equal its uri's stem. The
  uri here points at a PNG copy (viewers can't open DDS); FFNx only uses the name. Optional <name>_nml.dds and
  <name>_pbr.dds next to it are its normal and PBR maps with enable_lighting.
- Drawing: external textures are alpha blended (src alpha, 1 - src alpha) and alpha 0 is discarded; doubleSided
  turns culling off (else a shape is drawn with FFNx's RendererCullMode::FRONT).
- Skin: skins[0] is required for field parts (FFNx sets skins[0]'s animation unchecked). Its joints are read in the
  order of a depth-first walk of the joint nodes from joints[0], so joints must be listed in that order and joint
  nodes must have only joints as children. inverseBindMatrices are read per joint (column-major, glTF's).
- Animations: matched to the game's animation by the first 4 letters of their name, upper case (the game's .a file
  name). Every joint needs a translation and a rotation channel with one key per game frame (FFNx plays key n at the
  animation's frame n, ignoring times; a missing channel crashes or misplaces it). A joint's matrix is its parent's x
  T(translation) R(rotation) (glTF's TRS), the root's parent being the field's model scale / 128; a vertex is moved
  by its joints' matrices x their inverse bind matrices, weighted. An animation the glTF lacks leaves every joint at
  just the scale: the part is drawn unposed at the model's origin."""
import json
import struct
from pathlib import Path

import numpy as np
from PIL import Image

FLOAT, UBYTE, UINT = 5126, 5121, 5125
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963


def quaternion(m):
    """Unit quaternion (x, y, z, w) of a 3x3 rotation matrix (column vectors)."""
    m = np.asarray(m, np.float64)
    t = np.trace(m)
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        q = [(m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s, 0.25 * s]
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = [0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s, (m[2, 1] - m[1, 2]) / s]
    elif m[1, 1] > m[2, 2]:
        s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = [(m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s, (m[0, 2] - m[2, 0]) / s]
    else:
        s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = [(m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s, (m[1, 0] - m[0, 1]) / s]
    q = np.array(q)
    return q / np.linalg.norm(q)


def quaternions(m):
    """Unit quaternions (n, 4) (x, y, z, w) of rotation matrices (n, 3, 3)."""
    return np.array([quaternion(r) for r in np.asarray(m).reshape(-1, 3, 3)]).reshape(-1, 4)


def continuous(qs):
    """Quaternions (n, 4) with signs chosen so neighbours agree (viewers interpolate the short way)."""
    qs = np.array(qs, np.float64)
    for i in range(1, len(qs)):
        if qs[i] @ qs[i - 1] < 0:
            qs[i] = -qs[i]
    return qs


class Writer:
    """Collects buffers, accessors and the glTF's lists; write() saves <path>.gltf and <path>.bin."""

    def __init__(self):
        self.blobs, self.views, self.accessors = [], [], []
        self.doc = {"asset": {"version": "2.0", "generator": "ff7bg (FF7 Field Studio)"}, "scene": 0,
                    "scenes": [{"nodes": []}], "nodes": [], "meshes": [], "materials": [], "images": [],
                    "textures": [], "samplers": [], "skins": [], "animations": []}
        self._shared = {}

    def accessor(self, arr, ctype, typ, target=None, minmax=False, share=None):
        """An accessor with a bufferView of its own (FFNx reads from the view's start). share: a key under which an
        identical accessor is reused (for animation times, which FFNx doesn't read)."""
        if share is not None and share in self._shared:
            return self._shared[share]
        arr = np.ascontiguousarray(arr)
        data = arr.tobytes()
        off = sum(len(b) for b in self.blobs)
        if off % 4:
            self.blobs.append(b"\0" * (4 - off % 4))
            off += 4 - off % 4
        self.blobs.append(data)
        view = {"buffer": 0, "byteOffset": off, "byteLength": len(data)}
        if target:
            view["target"] = target
        self.views.append(view)
        a = {"bufferView": len(self.views) - 1, "componentType": ctype, "count": int(len(arr)), "type": typ}
        if minmax:
            v = arr.reshape(len(arr), -1).astype(np.float64)
            a["min"], a["max"] = v.min(0).tolist(), v.max(0).tolist()
        self.accessors.append(a)
        if share is not None:
            self._shared[share] = len(self.accessors) - 1
        return len(self.accessors) - 1

    def node(self, **kw):
        self.doc["nodes"].append(kw)
        return len(self.doc["nodes"]) - 1

    def image(self, uri, name):
        """An image by uri; FFNx goes by its name (the uri's stem)."""
        if not self.doc["samplers"]:
            self.doc["samplers"].append({"magFilter": 9729, "minFilter": 9729, "wrapS": 33071, "wrapT": 33071})
        self.doc["images"].append({"uri": uri, "name": name})
        self.doc["textures"].append({"source": len(self.doc["images"]) - 1, "sampler": 0})
        return len(self.doc["textures"]) - 1

    def material(self, texture=None, color=(1, 1, 1, 1), double_sided=True, name=None):
        m = {"pbrMetallicRoughness": {"baseColorFactor": [float(c) for c in color], "metallicFactor": 0.0,
                                      "roughnessFactor": 1.0}, "doubleSided": bool(double_sided)}
        if texture is not None:
            m["pbrMetallicRoughness"]["baseColorTexture"] = {"index": texture}
        if name:
            m["name"] = name
        self.doc["materials"].append(m)
        return len(self.doc["materials"]) - 1

    def primitive(self, pos, normal, uv, indices, material, joints=None, weights=None):
        """A primitive's attributes and indices (one bufferView each)."""
        n = len(pos)
        attrs = {"POSITION": self.accessor(np.asarray(pos, np.float32), FLOAT, "VEC3", ARRAY_BUFFER, True),
                 "NORMAL": self.accessor(np.asarray(normal, np.float32), FLOAT, "VEC3", ARRAY_BUFFER)}
        if uv is not None:
            attrs["TEXCOORD_0"] = self.accessor(np.asarray(uv, np.float32), FLOAT, "VEC2", ARRAY_BUFFER)
        if joints is not None:
            attrs["JOINTS_0"] = self.accessor(np.asarray(joints, np.uint8).reshape(n, 4), UBYTE, "VEC4", ARRAY_BUFFER)
            attrs["WEIGHTS_0"] = self.accessor(np.asarray(weights, np.float32).reshape(n, 4), FLOAT, "VEC4",
                                               ARRAY_BUFFER)
        idx = self.accessor(np.asarray(indices, np.uint32).reshape(-1), UINT, "SCALAR", ELEMENT_ARRAY_BUFFER)
        return {"attributes": attrs, "indices": idx, "material": material, "mode": 4}

    def write(self, path):
        path = Path(path)
        doc = {k: v for k, v in self.doc.items() if v != []}
        doc["accessors"], doc["bufferViews"] = self.accessors, self.views
        blob = b"".join(self.blobs)
        doc["buffers"] = [{"uri": path.stem + ".bin", "byteLength": len(blob)}]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.with_suffix(".bin").write_bytes(blob)
        path.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
        return path


def write_dds(path, rgba):
    """An uncompressed 32-bit BGRA DDS, one mip level (what FFNx's external meshes have loaded so far)."""
    rgba = np.ascontiguousarray(rgba, np.uint8)
    h, w = rgba.shape[:2]
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x8  # caps, height, width, pixel format, pitch
    hdr = struct.pack("<4sIIIIIII44x", b"DDS ", 124, flags, h, w, w * 4, 0, 1)
    pf = struct.pack("<IIIIIIII", 32, 0x41, 0, 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
    caps = struct.pack("<IIII4x", 0x1000, 0, 0, 0)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(hdr + pf + caps + rgba[..., [2, 1, 0, 3]].tobytes())


def read_dds(path):
    """RGBA uint8 of a write_dds file."""
    b = Path(path).read_bytes()
    h, w = struct.unpack_from("<II", b, 12)
    return np.frombuffer(b, np.uint8, w * h * 4, 128).reshape(h, w, 4)[..., [2, 1, 0, 3]].copy()


def write_texture(folder, name, rgba):
    """<folder>/<name>.dds for FFNx and <folder>/<name>.png for viewers: (dds path, png path)."""
    folder = Path(folder)
    write_dds(folder / f"{name}.dds", rgba)
    Image.fromarray(np.ascontiguousarray(rgba, np.uint8)).save(folder / f"{name}.png")
    return folder / f"{name}.dds", folder / f"{name}.png"


def read(path):
    """A written .gltf: (document dict, accessor reader (index -> array)). For checks: it reads accessors the way
    FFNx does, from their bufferView's start, ignoring offsets and strides."""
    path = Path(path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    blob = (path.parent / doc["buffers"][0]["uri"]).read_bytes()
    comps = {FLOAT: np.float32, UBYTE: np.uint8, UINT: np.uint32, 5123: np.uint16}
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}

    def get(i):
        a = doc["accessors"][i]
        v = doc["bufferViews"][a["bufferView"]]
        dt = np.dtype(comps[a["componentType"]])
        n = a["count"] * width[a["type"]]
        out = np.frombuffer(blob, dt, n, v["byteOffset"])
        return out.reshape(a["count"], -1) if width[a["type"]] > 1 else out
    return doc, get
