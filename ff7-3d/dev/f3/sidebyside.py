"""Crops of the game shots side by side: 3D before | 3D after | AI 2D (| original), labelled.

    python sidebyside.py <field> x1 y1 x2 y2 out.png [scale] [--orig]
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

G = Path(r"C:\Users\you\Desktop\ff7-3d-dev-f3\evidence\game")
name, x1, y1, x2, y2, out = sys.argv[1], *map(int, sys.argv[2:6]), sys.argv[6]
k = float(sys.argv[7]) if len(sys.argv) > 7 and not sys.argv[7].startswith("--") else 1.0
kinds = [("3d_before", "3D before"), ("3d_after", "3D after"), ("ai2d", "AI 2D")]
if "--orig" in sys.argv:
    kinds.append(("original", "original"))
crops = []
for kind, label in kinds:
    im = Image.open(G / f"{name}_{kind}.png").convert("RGB").crop((x1, y1, x2, y2))
    im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
    ImageDraw.Draw(im).rectangle((0, 0, 8 * len(label) + 8, 16), fill=(0, 0, 0))
    ImageDraw.Draw(im).text((4, 2), label, fill=(255, 255, 0))
    crops.append(im)
w, h = crops[0].size
sheet = Image.new("RGB", (len(crops) * (w + 6) - 6, h), "white")
for i, c in enumerate(crops):
    sheet.paste(c, (i * (w + 6), 0))
sheet.save(out)
print(out, sheet.size)
