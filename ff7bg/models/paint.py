"""Paint a model's or battle scene's textures from restyled views of it (docs/models-plan.md, "paint and project").

The textures are cut-up sheets the AI can't make sense of on their own, so the AI paints pictures of the model
instead: views rendered from cameras the game uses (a battle scene's) or around it (a model's). Each view's pixels
go back into the textures by the texel they show, scattered (one texel can be many places on screen: battle scenes
tile their floors and walls, models mirror their halves), weighted by how squarely and how closely the view sees it.

Views after the first only repaint what they're the first to see well (view_mask): the rest of the view shows the
paint so far, so the AI continues it instead of starting a different painting (Text2Tex). Texels no view sees keep
the original, enlarged, coloured like the paint around them."""
import cv2
import numpy as np
from PIL import Image

from . import render

BETTER = 1.3     # a view repaints a texel it sees this much better than the views before it did
MIN_NEW = 0.002  # ...and runs at all when that's at least this share of the view
OVERSAMPLE = 2   # each view's pixels are split this many ways per side when scattered (fewer holes at 4x)
REPEAT = 1.6     # repeats(): a texture the scene lays this many times over (on average per texel) is painted as a sheet
FEATHER = 10     # a later view's new paint fades in over this many pixels from its mask's edge (no hard seams)
MASK_GROW = 8    # ...and the AI repaints this much beyond the mask, into the paint so far, to meet it


def repeats(mesh, grid=256):
    """Per mesh texture how many times the scene lays its texels on average: the triangles' UV area (in textures,
    tiling counted) over the share of the texture they cover at all. Ground and wall tiles laid over a whole arena come
    out well above REPEAT; a sky strip or a prop's own sheet near 1. Projected, a repeated texture got every place's
    different painting averaged into one texel: battle scene oo's plain came out as mush (2026-11-07)."""
    out = []
    for k in range(len(mesh.textures)):
        sel = mesh.tex == k
        if not sel.any():
            out.append(0.0)
            continue
        uv = np.nan_to_num(mesh.uv[sel].astype(np.float64))
        area = 0.5 * np.abs((uv[:, 1, 0] - uv[:, 0, 0]) * (uv[:, 2, 1] - uv[:, 0, 1]) -
                            (uv[:, 2, 0] - uv[:, 0, 0]) * (uv[:, 1, 1] - uv[:, 0, 1]))
        cov = np.zeros((grid, grid), np.uint8)
        for tri in uv:
            t = tri - np.floor(tri.min(0))  # into the first tile
            if (t.max(0) >= 2).any():  # spans a whole tile: all of it
                cov[:] = 1
                break
            pts = np.round(t * grid).astype(np.int32)
            for dx in (0, -grid):  # and its part past the tile's edge, wrapped
                for dy in (0, -grid):
                    cv2.fillConvexPoly(cov, pts + [dx, dy], 1)
        out.append(float(area.sum() / max(cov.mean(), 1.0 / grid ** 2)))
    return out


def wraps(mesh, tol=0.02):
    """Per mesh texture (u wraps, v wraps): does the scene lay it so its left edge meets its right (or top its bottom)?
    Found where two of its triangles share an edge in the scene and their texture coordinates there differ by a whole
    texture across. A sheet is wrap-padded only across those (Restyler.sheet): oo's sky goes round the dome
    (left meets right) but its top doesn't meet its bottom, and painted wrapped both ways it got a seam across it."""
    out = [[False, False] for _ in mesh.textures]
    pos = np.round(mesh.pos.reshape(-1, 3) / 4.0).astype(np.int64)  # 4 units: the same vertex in two parts
    seen = {}
    for t in range(len(mesh)):
        k = int(mesh.tex[t])
        if k < 0:
            continue
        for i in range(3):
            key = (k, tuple(pos[t * 3 + i]))
            uv = mesh.uv[t, i]
            if np.isnan(uv).any():
                continue
            prev = seen.get(key)
            if prev is None:
                seen[key] = [uv]
                continue
            for p in prev:
                d = np.abs(uv - p)
                for a in (0, 1):
                    if abs(d[a] - round(d[a])) < tol and round(d[a]) >= 1:
                        out[k][a] = True
            if len(prev) < 4:
                prev.append(uv)
    return [tuple(w) for w in out]


def feather(mask, radius=FEATHER):
    """A mask's weight fading in from 0 at its edge to 1 at `radius` pixels inside it."""
    d = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 3)
    return np.clip(d / max(radius, 1), 0, 1).astype(np.float32)


def enlarge(img, scale):
    """A texture (RGBA uint8) at `scale`, smoothly (Lanczos); the alpha stays hard."""
    h, w = img.shape[:2]
    big = np.asarray(Image.fromarray(img[..., :3]).resize((w * scale, h * scale), Image.LANCZOS))
    a = np.asarray(Image.fromarray(img[..., 3]).resize((w * scale, h * scale), Image.NEAREST))
    return np.dstack([big, a])


def sample(img, uv):
    """Bilinear samples (n, c) float32 of an image (h, w, c) at uv (n, 2) in 0..1 (wrapped)."""
    h, w = img.shape[:2]
    x = np.mod(uv[:, 0], 1.0) * w - 0.5
    y = np.mod(uv[:, 1], 1.0) * h - 0.5
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    im = img.astype(np.float32)
    g = lambda yy, xx: im[np.mod(yy, h), np.mod(xx, w)]
    return (g(y0, x0) * (1 - fx) * (1 - fy) + g(y0, x0 + 1) * fx * (1 - fy) +
            g(y0 + 1, x0) * (1 - fx) * fy + g(y0 + 1, x0 + 1) * fx * fy)


def shade(mesh, r, images, background=(0, 0, 0)):
    """The view as the textures `images` (per mesh texture, RGBA of any size) paint it, sampled smoothly; untextured
    pixels from their vertex colours. (render.shade samples the nearest texel: for the original's look.)"""
    H, W = r.tri.shape
    out = np.zeros((H, W, 3), np.float32)
    out[:] = background
    hit = r.tri >= 0
    t = r.tri[hit]
    col = np.einsum("nk,nkc->nc", r.bary[hit], mesh.col[t].astype(np.float32))[:, :3]
    ti, uv = render.texels(mesh, r)
    tih, uvh = ti[hit], uv[hit]
    for k in np.unique(tih[tih >= 0]):
        sel = tih == k
        col[sel] = sample(images[k][..., :3], uvh[sel])
    out[hit] = col
    return np.clip(out, 0, 255).astype(np.uint8)


def quality(mesh, r, cam, sizes):
    """Per pixel how well the view sees the texel there (0 for nothing, untextured or cut out): how squarely
    (render.facing) times how closely (pixels per texel, at most 1), with each texture at sizes[k] (w, h)."""
    ti, uv = render.texels(mesh, r)
    H, W = ti.shape
    q = np.zeros((H, W), np.float32)
    ok = ti >= 0
    if not ok.any():
        return q, ti, uv
    tw = np.array([s[0] for s in sizes], np.float32)[np.maximum(ti, 0)]
    th = np.array([s[1] for s in sizes], np.float32)[np.maximum(ti, 0)]
    # texels per pixel from neighbouring pixels on the same texture (unwrapped differences)
    du = np.diff(uv[..., 0], axis=1, append=uv[:, -1:, 0]) * tw
    dv = np.diff(uv[..., 1], axis=0, append=uv[-1:, :, 1]) * th
    du2 = np.diff(uv[..., 1], axis=1, append=uv[:, -1:, 1]) * th
    dv2 = np.diff(uv[..., 0], axis=0, append=uv[-1:, :, 0]) * tw
    det = np.abs(du * dv - du2 * dv2)
    det = np.where((det > 0) & (det < 50), det, np.nan)  # wraps and edges: borrow from around
    det = cv2.blur(np.nan_to_num(det, nan=0), (3, 3)) / np.maximum(cv2.blur((~np.isnan(det)).astype(np.float32), (3, 3)), 1e-3)
    density = np.minimum(1.0, 1.0 / np.maximum(det, 1e-6))
    q[ok] = (render.facing(mesh, r, cam) * density)[ok]
    return q, ti, uv


class Painter:
    """The textures being painted: per mesh texture the colour so far (float), the weight it has, and the best view
    quality any view had for each texel."""

    def __init__(self, mesh, scale=4, base=None):
        self.mesh, self.scale = mesh, scale
        self.base = base or [enlarge(t.tex.rgba(t.palette), scale) for t in mesh.textures]
        self.fixed = {}  # {texture: RGBA} painted as sheets (repeated ones): shown as they are, never projected into
        self.sizes = [(b.shape[1], b.shape[0]) for b in self.base]
        self.acc = [np.zeros(b.shape[:2] + (3,), np.float32) for b in self.base]
        self.wsum = [np.zeros(b.shape[:2], np.float32) for b in self.base]
        self.best = [np.zeros(b.shape[:2], np.float32) for b in self.base]

    def images(self, filled=True):
        """Per texture RGBA uint8: the paint where there is some, the original elsewhere (filled: coloured like the
        paint around it)."""
        out = []
        for k, b in enumerate(self.base):
            if k in self.fixed:
                out.append(self.fixed[k])
                continue
            w = self.wsum[k]
            col = b[..., :3].astype(np.float32)
            have = w > 0
            if have.any():
                paint = self.acc[k] / np.maximum(w, 1e-6)[..., None]
                if filled and not have.all():
                    col = _match(col, paint, have)
                col[have] = paint[have]
            out.append(np.dstack([np.clip(col, 0, 255).astype(np.uint8), b[..., 3]]))
        return out

    def view_mask(self, cam, r):
        """Pixels of this view to repaint: the texels it sees better (BETTER) than any view before, or first."""
        q, ti, uv = quality(self.mesh, r, cam, self.sizes)
        mask = np.zeros(q.shape, bool)
        for k in np.unique(ti[ti >= 0]):
            if k in self.fixed:
                continue
            sel = ti == k
            h, w = self.best[k].shape
            x = (uv[sel][:, 0] * w).astype(int).clip(0, w - 1)
            y = (uv[sel][:, 1] * h).astype(int).clip(0, h - 1)
            prev = self.best[k][y, x]
            mask[sel] = (q[sel] > 0.05) & ((prev <= 0) | (q[sel] > prev * BETTER))
        # whole regions, not speckle: close small gaps and drop crumbs
        m = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        return (m > 0) & (r.tri >= 0)

    def add(self, cam, r, image, mask=None, power=4.0):
        """Scatter a (restyled) view into the textures: its pixels inside `mask` (all it sees when None; a float mask
        weights them, feather's fade at its edge), each into the texels around where it lands (bilinear splat),
        weighted by quality ** power so the best view wins and the views blend only where they're nearly as good.
        Textures painted as sheets (fixed) take nothing."""
        H, W = r.tri.shape
        s = OVERSAMPLE
        if mask is not None:
            mask = mask.astype(np.float32)
        if s > 1:  # the view's pixels split s ways per side: their uv from a finer raster of the same camera
            fine = render.Camera(cam.eye, cam.target, cam.up, cam.fov_y, W * s, H * s, cam.near,
                                 cam.focal * s if cam.focal else None)
            rf = render.rasterize(self.mesh, fine)
            img = cv2.resize(image, (W * s, H * s), interpolation=cv2.INTER_CUBIC)
            msk = None if mask is None else cv2.resize(mask, (W * s, H * s), interpolation=cv2.INTER_LINEAR)
            cam, r, image, mask = fine, rf, img, msk
        q, ti, uv = quality(self.mesh, r, cam, self.sizes)
        use = (ti >= 0) & (q > 0.02)
        if mask is not None:
            use &= mask > 0
        img = image.astype(np.float32)
        for k in np.unique(ti[use]):
            if k in self.fixed:
                continue
            sel = use & (ti == k)
            h, w = self.best[k].shape
            x = uv[sel][:, 0] * w - 0.5
            y = uv[sel][:, 1] * h - 0.5
            wq = q[sel] ** power * (mask[sel] if mask is not None else 1.0)
            col = img[sel]
            x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
            fx, fy = x - x0, y - y0
            for dx, dy, f in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
                xx, yy = np.mod(x0 + dx, w), np.mod(y0 + dy, h)
                ww = wq * f
                np.add.at(self.wsum[k], (yy, xx), ww)
                for c in range(3):
                    np.add.at(self.acc[k][..., c], (yy, xx), col[:, c] * ww)
                np.maximum.at(self.best[k], (yy, xx), q[sel] * (f > 0.25))

    def fill_holes(self, radius=6):
        """Texels between scattered samples (a view saw the texture smaller than 1 pixel per texel at 4x): filled
        from the paint around them, within `radius` texels (push-pull); further ones stay the original's."""
        for k in range(len(self.base)):
            w = self.wsum[k]
            if not (w > 0).any():
                continue
            acc, ws = self.acc[k].copy(), w.copy()
            for _ in range(int(np.ceil(np.log2(max(radius, 1)))) + 1):
                hole = ws <= 0
                if not hole.any():
                    break
                a = cv2.blur(acc, (3, 3))
                b = cv2.blur(ws, (3, 3))
                acc[hole] = a[hole]
                ws[hole] = b[hole]
            grown = (ws > 0) & (w <= 0)
            scale = 1e-3 / np.maximum(ws[grown], 1e-12)  # tiny weight: any real sample overrides it later
            self.acc[k][grown] = acc[grown] * scale[:, None]
            self.wsum[k][grown] = ws[grown] * scale


MATCH_GAIN = (0.7, 1.4)  # _match: the most a channel's gain may change


def _match(orig, paint, have):
    """The original (float RGB) recoloured like the paint where both are, for texels no view painted: they then sit
    with the paint instead of standing out in the original's colours. Per channel gain and offset, the gain held to
    MATCH_GAIN: a full colour matrix fitted on one texture's painted part turned oq's pale pipes yellow."""
    o, p = orig[have], paint[have]
    if len(o) < 50:
        return orig
    out = orig.copy()
    for c in range(3):
        a, b = np.polyfit(o[:, c], p[:, c], 1)
        a = float(np.clip(a, *MATCH_GAIN))
        b = float(np.mean(p[:, c]) - a * np.mean(o[:, c]))
        out[..., c] = orig[..., c] * a + b
    return out


def paint(mesh, cameras, restyle, scale=4, progress=None, base=None, sheets=None, on_sheet=None, repeat=REPEAT,
          panorama=False, detail=1, seams=False, centre=None):
    """Paint the mesh's textures: the repeated ones as sheets, the rest from restyled views.

    restyle(original view, current view or None, mask or None) -> RGB: the first call (current None) restyles the whole
    view; later ones repaint only inside the mask, around the current paint. base: the original textures enlarged by
    `scale` (RGBA, per mesh texture; default Lanczos), what the views show before painting.

    sheets(texture RGBA, its name) -> RGBA (the Restyler's sheet): the textures the scene lays many times over
    (repeats, REPEAT: a battle scene's ground and wall tiles) are painted flat, wrap-padded so their tiles stay
    seamless, before any view (restyle.prepare(the first view) gives them the scene's description first); the views
    then show them painted and project nothing into them. on_sheet(i, n, name) reports them. Without sheets, all of it
    is projected (as before 2026-11-07). repeat: the threshold; 0 paints every texture as a sheet (battle scenes: their
    textures are almost all laid 2-20 times over, and a projection only looks right from its own cameras while the
    battle's cameras swing all round; user, 2026-11-07).

    With sheets (scenepaint, battle scenes):
    - panorama: the far textures (scenepaint.classify: sky dome, far mountains) are painted together as one picture of
      the view all round from `centre` (x, eye y, z; default the cameras' look-at points, averaged, at their eyes'
      height) via restyle.panorama, whatever their repeats: no seams between the sky's bands;
    - detail: ground textures (classify) painted this many times bigger, with the detail pass (sheets(..., detail=));
    - seams: afterwards the sheet-painted textures' colours are pulled to meet where pieces meet in the scene
      (scenepaint.level_seams).

    Returns (Painter, [per view: dict(camera, mask share, image)], with log[0].get("sheets") the sheets' names,
    log[0]["panorama"] the panorama-painted ones and log[0]["detail"] the ground ones painted bigger)."""
    from . import scenepaint
    p = Painter(mesh, scale, base)
    log = []
    pano, ground = [], []
    if sheets is not None:
        rep = repeats(mesh)
        cls = scenepaint.classify(mesh)
        todo = [k for k, n in enumerate(rep) if n > 0 and n >= repeat]
        far = [k for k in range(len(mesh.textures)) if cls[k]["far"]] if panorama and hasattr(restyle, "panorama") else []
        if todo or far:
            if hasattr(restyle, "prepare") and cameras:
                r0 = render.rasterize(mesh, cameras[0])
                restyle.prepare(shade(mesh, r0, p.base))
        if far:
            if centre is None:
                tg = np.mean([np.asarray(c.target, float) for c in cameras], 0) if cameras else np.zeros(3)
                ey = float(np.median([c.eye[1] for c in cameras])) if cameras else -1500.0
                centre = (tg[0], ey, tg[2])
            P = scenepaint.Panorama(mesh, far, centre, scenepaint.panorama_period(mesh, far, rep))
            if on_sheet:
                on_sheet(0, len([k for k in todo if k not in far]) + 1, "the sky and far scenery, as one panorama")
            img, _ = P.draw(p.base)
            painted = restyle.panorama(img)
            p.fixed.update(P.back(painted, p.base))
            pano = list(far)
        todo = [k for k in todo if k not in pano]
        if todo:
            wr = wraps(mesh)
            off = 1 if pano else 0
            for i, k in enumerate(todo):
                if on_sheet:
                    on_sheet(i + off, len(todo) + off, mesh.textures[k].name)
                d = detail if detail > 1 and cls[k]["ground"] else 1
                p.fixed[k] = sheets(p.base[k], mesh.textures[k].name, wrap=wr[k], **({"detail": d} if d > 1 else {}))
                if d > 1:
                    ground.append(k)
    shown = [p.fixed.get(k, b) for k, b in enumerate(p.base)]  # what the views start from
    if all(k in p.fixed for k in np.unique(mesh.tex[mesh.tex >= 0])):  # all of it painted as sheets: no views
        cameras = []
    for i, cam in enumerate(cameras):
        r = render.rasterize(mesh, cam)
        if not (r.tri >= 0).any():
            continue
        orig = shade(mesh, r, shown)
        if not any((w > 0).any() for w in p.wsum):
            mask = r.tri >= 0
            out = restyle(orig, None, None)
            weight = None
        else:
            mask = p.view_mask(cam, r)
            if mask.mean() < MIN_NEW:
                log.append(dict(camera=cam, new=float(mask.mean()), image=None))
                continue
            cur = shade(mesh, r, p.images())
            # the AI repaints a little beyond the mask into the paint so far, and the new paint fades in from the
            # mask's edge: oo's later views left hard steps in the clouds where they met the first one
            grown = cv2.dilate(mask.astype(np.uint8), np.ones((2 * MASK_GROW + 1,) * 2, np.uint8)) > 0
            out = restyle(orig, cur, grown & (r.tri >= 0))
            weight = feather(mask)
        p.add(cam, r, out, weight)
        log.append(dict(camera=cam, new=float(mask.mean()), image=out))
        if progress:
            progress(i + 1, len(cameras))
    p.fill_holes()
    if seams and p.fixed:
        imgs = scenepaint.level_seams(mesh, p.images(), ks=list(p.fixed))
        for k in p.fixed:
            p.fixed[k] = imgs[k]
    if log or p.fixed:
        log = log or [dict(camera=None, new=0.0, image=None)]
        log[0]["sheets"] = [mesh.textures[k].name for k in sorted(p.fixed)]
        log[0]["panorama"] = [mesh.textures[k].name for k in pano]
        log[0]["detail"] = [mesh.textures[k].name for k in ground]
    return p, log
