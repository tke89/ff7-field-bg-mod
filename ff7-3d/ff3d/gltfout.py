"""Write .gltf + .bin the way FFNx's external mesh loader reads them: one tightly packed bufferView per accessor
(it ignores accessor offsets and strides), NORMAL on every primitive, textures as image uris (the loader then opens
mesh/field/textures/<image name>.dds), materials looked up by image name."""
import json, struct
from pathlib import Path
import numpy as np


def write(path, prims, images=(), bones=0, anim=None):
    """prims: [{pos (n,3), nrm (n,3), uv (n,2) or None, idx (m,), color (r,g,b,a) or None, image index or None,
    double_sided, optional joints (n,4) and weights (n,4)}].
    bones: extra joints under the root (joint 0 is the root, joints 1..bones its children).
    anim: {"name": game animation name (FFNx matches the first 4 letters), "translation": (frames, 1 + bones, 3)}.
    FFNx plays it one keyframe per game frame and needs rotation and translation keys on every joint."""
    path = Path(path)
    blobs, views, accs = [], [], []

    def add(arr, ctype, typ, target, minmax=False):
        data = np.ascontiguousarray(arr).tobytes()
        off = sum(len(b) for b in blobs)
        pad = (-off) % 4
        if pad:
            blobs.append(b"\0" * pad)
            off += pad
        blobs.append(data)
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(data), **({"target": target} if target else {})})
        a = {"bufferView": len(views) - 1, "componentType": ctype, "count": len(arr), "type": typ}
        if minmax:
            v = np.asarray(arr).reshape(len(arr), -1)  # glTF wants arrays, also for scalars
            a["min"], a["max"] = v.min(0).tolist(), v.max(0).tolist()
        accs.append(a)
        return len(accs) - 1

    meshes, materials = [], []
    for i, p in enumerate(prims):
        attrs = {"POSITION": add(p["pos"].astype(np.float32), 5126, "VEC3", 34962, True),
                 "NORMAL": add(p["nrm"].astype(np.float32), 5126, "VEC3", 34962)}
        if p.get("uv") is not None:
            attrs["TEXCOORD_0"] = add(p["uv"].astype(np.float32), 5126, "VEC2", 34962)
        mat = {"name": f"m{i}", "pbrMetallicRoughness": {"baseColorFactor": list(p.get("color") or (1, 1, 1, 1)),
                                                          "metallicFactor": 0, "roughnessFactor": 1},
               "doubleSided": bool(p.get("double_sided", True))}
        if p.get("image") is not None:
            mat["pbrMetallicRoughness"]["baseColorTexture"] = {"index": p["image"]}
        # FFNx draws field meshes skinned and needs a skin: every vertex fully on one root joint
        n = len(p["pos"])
        joints = p.get("joints", np.zeros((n, 4), np.uint8))
        weights = p.get("weights", np.tile(np.array([1, 0, 0, 0], np.float32), (n, 1)))
        attrs["JOINTS_0"] = add(joints.astype(np.uint8), 5121, "VEC4", 34962)
        attrs["WEIGHTS_0"] = add(weights.astype(np.float32), 5126, "VEC4", 34962)
        materials.append(mat)
        meshes.append({"primitives": [{"attributes": attrs, "indices": add(p["idx"].astype(np.uint32), 5125, "SCALAR", 34963),
                                       "material": i}]})
    ibm = add(np.tile(np.eye(4, dtype=np.float32).reshape(1, 16), (1 + bones, 1)), 5126, "MAT4", None)
    root = len(meshes)
    joint_nodes = [{"name": "root", "translation": [0, 0, 0], "rotation": [0, 0, 0, 1],
                    **({"children": [root + 1 + b for b in range(bones)]} if bones else {})}]
    joint_nodes += [{"name": f"wind{b}", "translation": [0, 0, 0], "rotation": [0, 0, 0, 1]} for b in range(bones)]
    animations = []
    if anim is not None:
        tr = np.asarray(anim["translation"], np.float32)
        frames = len(tr)
        times = add(np.arange(frames, dtype=np.float32) / 30, 5126, "SCALAR", None, True)
        rot = add(np.tile(np.array([0, 0, 0, 1], np.float32), (frames, 1)), 5126, "VEC4", None)
        samplers, channels = [], []
        for j in range(1 + bones):
            samplers.append({"input": times, "output": add(tr[:, j], 5126, "VEC3", None), "interpolation": "LINEAR"})
            channels.append({"sampler": len(samplers) - 1, "target": {"node": root + j, "path": "translation"}})
            samplers.append({"input": times, "output": rot, "interpolation": "LINEAR"})
            channels.append({"sampler": len(samplers) - 1, "target": {"node": root + j, "path": "rotation"}})
        animations.append({"name": anim["name"], "samplers": samplers, "channels": channels})
    gltf = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": list(range(len(meshes))) + [root]}],
            "nodes": [{"mesh": i, "skin": 0, "name": f"mesh{i}"} for i in range(len(meshes))] + joint_nodes,
            "skins": [{"joints": [root + j for j in range(1 + bones)], "inverseBindMatrices": ibm, "name": "skin"}],
            **({"animations": animations} if animations else {}),
            "meshes": meshes, "materials": materials,
            "accessors": accs, "bufferViews": views,
            "buffers": [{"uri": path.stem + ".bin", "byteLength": sum(len(b) for b in blobs)}]}
    if images:
        gltf["images"] = [{"uri": u, "name": Path(u).stem} for u in images]
        gltf["textures"] = [{"source": i} for i in range(len(images))]
    path.with_suffix(".bin").write_bytes(b"".join(blobs))
    path.write_text(json.dumps(gltf))


def box(c0, c1):
    """An axis-aligned box from corner c0 to c1: (pos, nrm, idx) with flat normals."""
    x0, y0, z0 = c0
    x1, y1, z1 = c1
    faces = [((1, 0, 0), [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)]),
             ((-1, 0, 0), [(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)]),
             ((0, 1, 0), [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)]),
             ((0, -1, 0), [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)]),
             ((0, 0, 1), [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]),
             ((0, 0, -1), [(x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)])]
    pos, nrm, idx = [], [], []
    for n, quad in faces:
        b = len(pos)
        pos += quad
        nrm += [n] * 4
        idx += [b, b + 1, b + 2, b, b + 2, b + 3]
    return np.array(pos, float), np.array(nrm, float), np.array(idx)


def write_dds(path, rgba):
    """Uncompressed 32-bit BGRA DDS (what FFNx's bimg loader reads for external mesh textures)."""
    import numpy as np
    h, w = rgba.shape[:2]
    hdr = struct.pack("<4sIIIIIII44x", b"DDS ", 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x8, h, w, w * 4, 0, 1)
    pf = struct.pack("<IIIIIII", 32, 0x41, 0, 32, 0x00FF0000, 0x0000FF00, 0x000000FF) + struct.pack("<I", 0xFF000000)
    caps = struct.pack("<IIII4x", 0x1000, 0, 0, 0)
    bgra = np.ascontiguousarray(rgba[..., [2, 1, 0, 3]]).astype(np.uint8)
    Path(path).write_bytes(hdr + pf + caps + bgra.tobytes())


def write_dds_f16(path, rgba):
    """RGBA16F DDS (DX10 header, DXGI_FORMAT_R16G16B16A16_FLOAT): values above 1 allowed."""
    import numpy as np
    h, w = rgba.shape[:2]
    hdr = struct.pack("<4sIIIIIII44x", b"DDS ", 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x8, h, w, w * 8, 0, 1)
    pf = struct.pack("<IIIIIIII", 32, 0x4, struct.unpack("<I", b"DX10")[0], 0, 0, 0, 0, 0)
    caps = struct.pack("<IIII4x", 0x1000, 0, 0, 0)
    dx10 = struct.pack("<IIIII", 10, 3, 0, 1, 0)  # format, TEXTURE2D, misc, array size, misc2
    Path(path).write_bytes(hdr + pf + caps + dx10 + np.ascontiguousarray(rgba, dtype=np.float16).tobytes())


def read_dds(path):
    """RGBA uint8 of a write_dds file."""
    import numpy as np
    b = Path(path).read_bytes()
    h, w = struct.unpack_from("<II", b, 12)
    return np.frombuffer(b, np.uint8, w * h * 4, 128).reshape(h, w, 4)[..., [2, 1, 0, 3]].copy()
