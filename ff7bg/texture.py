"""The "SYW + Digital Art on rock and rough ground" start: the SYW pack's 4x of the original art, with Upscayl's
Digital Art enlargement where the art is organic texture.

The SYW pack keeps lettering, brick, cobbles and machinery crisp, but it also sharpens the Northern Crater's
procedural rock and floor textures, and MOSS paints that grain as outlined cells: las1_3's floor became netting.
Upscayl's Digital Art model flattens grain into clean shapes, which cleared las1_3's floor, but it also flattened
Kalm's cobbles and garbled its INN sign and md1_1's NORTH EDGE STATION lettering (2026-11-18 tests). So the start
takes Digital Art only where the original is busy at a fine scale and has no direction over a wider one (rock,
gravel, cave floors), and SYW everywhere else, the field's sign areas always."""
import cv2
import numpy as np

from . import upscalers

NAME = "syw+digital-art"  # the input_upscaler value
DART = "upscayl:digital-art-4x"
VERSION = 1  # bump when texture_mask changes: cached starts are made again


def texture_mask(img1x, boxes=(), offset=(0, 0), scale=1.0):
    """0..1 at img1x's size: how much a pixel is organic texture. boxes: the field's sign areas (canvas pixels),
    placed by offset (the image's left/top padding, canvas pixels) and scale (image pixels per canvas pixel)."""
    g = cv2.cvtColor(np.ascontiguousarray(img1x[..., :3]), cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3) / 8
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3) / 8

    def sm(a, s):
        return cv2.GaussianBlur(a, (0, 0), s * scale)

    busy = np.clip((np.sqrt(sm(gx * gx, 2) + sm(gy * gy, 2)) - 0.03) / 0.05, 0, 1)  # fine detail, any kind
    j11, j22, j12 = sm(gx * gx, 6), sm(gy * gy, 6), sm(gx * gy, 6)
    coherent = np.sqrt((j11 - j22) ** 2 + 4 * j12 ** 2) / (j11 + j22 + 1e-7)  # lines and stripes: one direction
    t = np.clip(sm(busy * np.clip((0.35 - coherent) / 0.2, 0, 1), 4) * 2, 0, 1)
    ox, oy = offset
    for b in boxes:  # signs keep the sharp start
        x1, y1, x2, y2 = (int(round((v + o) * scale)) for v, o in
                          ((b["x1"] - 4, ox), (b["y1"] - 4, oy), (b["x2"] + 4, ox), (b["y2"] + 4, oy)))
        t[max(0, y1):max(0, y2), max(0, x1):max(0, x2)] = 0
    return sm(t, 1.5)


def mixed(big, img1x, boxes=(), offset=(0, 0), cancelled=lambda: False):
    """The SYW start `big` with Upscayl Digital Art's enlargement of img1x (the same view at 1x) where texture_mask
    says, at big's size."""
    H, W = big.shape[:2]
    dart = upscalers.upscale(np.ascontiguousarray(img1x[..., :3]), DART, W, H, cancelled).astype(np.float32)
    m = texture_mask(img1x, boxes, offset)
    m = cv2.resize(m, (W, H), interpolation=cv2.INTER_LINEAR)[..., None]
    return np.clip(big.astype(np.float32) * (1 - m) + dart * m, 0, 255).astype(np.uint8)


def overlay(img1x, boxes=(), offset=(0, 0)):
    """The mask as an RGBA picture at img1x's size, for the viewer: orange where Digital Art is used."""
    m = texture_mask(img1x, boxes, offset)
    out = np.zeros(m.shape + (4,), np.uint8)
    out[..., 0], out[..., 1], out[..., 2] = 255, 140, 30
    out[..., 3] = np.round(m * 200).astype(np.uint8)
    return out
