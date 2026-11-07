"""Wind sway for eals_1's flowers through FFNx skinning: how much each grid vertex sways (flowers only, rising with
height above the bed), and the wind bones' motion."""
import numpy as np
import cv2
from PIL import Image


def flower_mask(render, GW, GH):
    """Yellow petals and green stems/leaves, as a soft mask on the vertex grid."""
    hsv = cv2.cvtColor(render, cv2.COLOR_RGB2HSV_FULL).astype(np.float32)
    h, s, v = hsv[..., 0] * 360 / 256, hsv[..., 1] / 255, hsv[..., 2] / 255
    petals = (h > 38) & (h < 68) & (s > 0.45) & (v > 0.55)
    leaves = (h > 75) & (h < 165) & (s > 0.35) & (v > 0.12)
    m = (petals | leaves).astype(np.float32)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    m = cv2.resize(m, (GW, GH), interpolation=cv2.INTER_AREA)
    return np.clip(cv2.GaussianBlur(m, (0, 0), 1.5) * 1.6, 0, 1)


def heights(Pw, ground_normals_up, flowers, sigma=10):
    """Height of each vertex above the ground around it: ground = upward-facing, non-flower vertices, their world z
    spread under the flowers by normalised convolution."""
    z = Pw[..., 2].astype(np.float32)
    w = (ground_normals_up & (flowers < 0.2)).astype(np.float32)
    num = cv2.GaussianBlur(z * w, (0, 0), sigma)
    den = cv2.GaussianBlur(w, (0, 0), sigma)
    ground = num / np.maximum(den, 1e-4)
    return np.where(den > 0.02, z - ground, 0)


def weights(flowers, h, tall=45.0):
    """0 at the bed, 1 at a flower top (`tall` world units up), only where there are flowers."""
    t = np.clip(h / tall, 0, 1)
    return flowers * t * t * (3 - 2 * t)


def bones(Pw, sway, count=10, wind=(1.0, 0.35)):
    """Bone positions along the wind direction over the swaying area, and each vertex's two bones + blend."""
    wd = np.array(wind) / np.linalg.norm(wind)
    a = Pw[..., 0] * wd[0] + Pw[..., 1] * wd[1]
    on = sway > 0.05
    lo, hi = np.percentile(a[on], 1), np.percentile(a[on], 99)
    x = np.clip((a - lo) / (hi - lo) * (count - 1), 0, count - 1 - 1e-6)
    i = np.floor(x).astype(int)
    return i, x - i, np.linspace(lo, hi, count), wd


def motion(frames, along, wd, amp=6.0, waves=(5, 8), gust=1, wavelength=700.0):
    """World offsets (frames, bones, 2): two travelling waves across the garden plus a slow gust, looping
    seamlessly over `frames` (whole cycles only)."""
    t = np.arange(frames)[:, None] / frames
    k = 2 * np.pi * (along[None, :] - along[0]) / wavelength
    s = (0.55 * np.sin(2 * np.pi * waves[0] * t - k) + 0.3 * np.sin(2 * np.pi * waves[1] * t - 1.7 * k + 1.3))
    g = 0.65 + 0.35 * np.sin(2 * np.pi * gust * t - 0.5 * k)  # gusts come and go
    d = amp * s * g + amp * 0.35 * g  # leans with the wind on average
    return d[..., None] * wd[None, None, :]
