"""Minimal ComfyUI API client (the desktop app on port 8000)."""
import json
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

HOST = "http://127.0.0.1:8000"


def call(path, data=None, ctype="application/json"):
    req = urllib.request.Request(HOST + path, data=data, headers={"Content-Type": ctype} if data else {})
    with urllib.request.urlopen(req) as r:
        return r.read()


def upload(path, name=None):
    """Put an image in ComfyUI's input folder (subfolder ff7_3d); returns the name LoadImage wants."""
    b = uuid.uuid4().hex
    name = name or Path(path).name
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            f"Content-Type: image/png\r\n\r\n").encode()
    body += Path(path).read_bytes()
    for k, v in (("overwrite", "true"), ("subfolder", "ff7_3d")):
        body += f"\r\n--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}".encode()
    body += f"\r\n--{b}--\r\n".encode()
    r = json.loads(call("/upload/image", body, f"multipart/form-data; boundary={b}"))
    return f"{r['subfolder']}/{r['name']}" if r.get("subfolder") else r["name"]


def run(workflow, timeout=1800):
    """Queue a workflow (API format) and wait; returns its outputs {node id: {...}}."""
    r = json.loads(call("/prompt", json.dumps({"prompt": workflow}).encode()))
    if r.get("node_errors"):
        raise RuntimeError(json.dumps(r["node_errors"])[:2000])
    pid = r["prompt_id"]
    end = time.time() + timeout
    while time.time() < end:
        h = json.loads(call(f"/history/{pid}"))
        if pid in h and h[pid].get("status", {}).get("completed") is not None:
            st = h[pid]["status"]
            if st.get("status_str") != "success":
                raise RuntimeError(json.dumps(st)[:2000])
            return h[pid]["outputs"]
        time.sleep(1.5)
    raise TimeoutError("ComfyUI job timed out")


def files(outputs, node):
    """File entries a node saved (images, 3d, ...)."""
    out = []
    for v in outputs.get(node, {}).values():
        if isinstance(v, list):
            out += [x for x in v if isinstance(x, dict) and "filename" in x]
    return out


def fetch(entry):
    q = urllib.parse.urlencode({"filename": entry["filename"], "subfolder": entry.get("subfolder", ""),
                                "type": entry.get("type", "output")})
    return call(f"/view?{q}")
