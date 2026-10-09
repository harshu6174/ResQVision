"""Synthetic demo data: a fake town with KNOWN damage, so we can test the pipeline.

Why synthetic? Real pre/post pairs are huge and need registration/licences.
With synthetic data we know the ground truth, so we can MEASURE accuracy and
debug each step. Real data (xBD, Vantor Open Data) plugs into the same pipeline.
The location is only a plausible coastal coordinate; the data is NOT real.
"""
from __future__ import annotations
import json, os
import cv2
import numpy as np
import pandas as pd

LON0, LAT0 = 82.2500, 16.9900          # top-left corner (synthetic demo)
GSD = 0.5                                 # metres per pixel
LEVELS = ["none", "minor", "major", "destroyed"]


def _noise(rng, h, w, sigma):
    n = rng.normal(0, 1, (h, w)).astype(np.float32)
    n = cv2.GaussianBlur(n, (0, 0), sigma)
    return n / (n.std() + 1e-6)


def make_demo(out_dir: str, size: int = 1024, seed: int = 7):
    os.makedirs(out_dir, exist_ok=True)
    rng = np.random.default_rng(seed)
    H = W = size
    # ---- ground texture ------------------------------------------------
    g = np.zeros((H, W, 3), np.float32)
    low = _noise(rng, H, W, 40)
    base = np.stack([112 + 14 * low, 128 + 12 * low, 84 + 8 * low], -1)
    g[:] = base + _noise(rng, H, W, 1.5)[..., None] * 4
    # ---- roads (grid) ----------------------------------------------------
    road_w = 14
    xs = [60, 260, 460, 660, 860]
    ys = [70, 280, 490, 700, 910]
    roads_px = []
    for x in xs: roads_px.append([(x, 0), (x, H)])
    for y in ys: roads_px.append([(0, y), (W, y)])
    for (a, b) in roads_px:
        cv2.line(g, a, b, (92, 92, 96), road_w)
        cv2.line(g, a, b, (210, 205, 190), 1)
    # ---- buildings in the blocks ----------------------------------------
    blds = []   # dict(x,y,w,h,color,kind)
    palette = [(150, 150, 155), (176, 84, 70), (205, 205, 200), (120, 100, 90), (90, 110, 140), (186, 140, 100)]
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            x0, x1 = xs[i] + 22, xs[i + 1] - 22
            y0, y1 = ys[j] + 22, ys[j + 1] - 22
            cx = x0
            while cx < x1 - 30:
                cy = y0
                bw = int(rng.integers(30, 52))
                while cy < y1 - 30:
                    bh = int(rng.integers(30, 52))
                    if cx + bw <= x1 and cy + bh <= y1 and rng.random() < 0.85:
                        blds.append(dict(x=cx, y=cy, w=bw, h=bh,
                                         color=palette[int(rng.integers(len(palette)))], kind="house"))
                    cy += bh + int(rng.integers(10, 20))
                cx += bw + int(rng.integers(10, 20))
    # special buildings: pick by centre proximity to chosen spots
    def nearest(px, py):
        d = [((b["x"] + b["w"] / 2 - px) ** 2 + (b["y"] + b["h"] / 2 - py) ** 2) for b in blds]
        return int(np.argmin(d))
    hosp = nearest(540, 380); blds[hosp].update(kind="hospital", color=(235, 235, 240), w=70, h=56)
    sch1 = nearest(340, 560); blds[sch1].update(kind="school", color=(200, 170, 90))
    sch2 = nearest(780, 780); blds[sch2].update(kind="school", color=(200, 170, 90))
    shel = nearest(150, 800); blds[shel].update(kind="shelter", color=(120, 160, 120))

    # ---- ground truth damage: epicentre with decay ------------------------
    ex, ey = 560, 430
    for b in blds:
        d = np.hypot(b["x"] + b["w"] / 2 - ex, b["y"] + b["h"] / 2 - ey)
        p = np.exp(-d / 230)
        r = rng.random()
        b["gt"] = "destroyed" if r < p * 0.45 else "major" if r < p * 0.80 else "minor" if r < p * 1.05 + 0.04 else "none"
    blds[hosp]["gt"] = "minor"          # a hospital with only minor damage (still matters!)
    blds[sch1]["gt"] = "major"
    blds[sch2]["gt"] = "none"
    blds[shel]["gt"] = "none"

    pre = g.copy()
    for b in blds:
        x, y, w, h = b["x"], b["y"], b["w"], b["h"]
        cv2.rectangle(pre, (x + 4, y + 5), (x + w + 4, y + h + 5), (60, 66, 52), -1)     # shadow
        roof = np.array(b["color"], np.float32) + _noise(rng, h, w, 2)[..., None] * 5
        pre[y:y + h, x:x + w] = roof
        cv2.rectangle(pre, (x, y), (x + w - 1, y + h - 1), (70, 70, 70), 1)
        cv2.line(pre, (x + w // 2, y), (x + w // 2, y + h), (110, 110, 110), 1)          # roof ridge
        if b["kind"] == "hospital":
            cv2.rectangle(pre, (x + w // 2 - 3, y + h // 2 - 10), (x + w // 2 + 3, y + h // 2 + 10), (200, 40, 40), -1)
            cv2.rectangle(pre, (x + w // 2 - 10, y + h // 2 - 3), (x + w // 2 + 10, y + h // 2 + 3), (200, 40, 40), -1)

    post = pre.copy()
    rub = lambda hh, ww: np.clip(np.stack([135, 120, 105], -1)[None, None] + rng.normal(0, 34, (hh, ww, 1)) +
                                  rng.normal(0, 14, (hh, ww, 3)), 0, 255).astype(np.float32)
    for b in blds:
        x, y, w, h = b["x"], b["y"], b["w"], b["h"]
        lvl = b["gt"]
        if lvl == "none":
            continue
        frac = {"minor": 0.28, "major": 0.62, "destroyed": 1.0}[lvl]
        mask = np.zeros((H, W), np.uint8)
        if lvl == "destroyed":
            cv2.rectangle(mask, (x - 3, y - 3), (x + w + 3, y + h + 3), 255, -1)
        else:
            k = max(2, int(w * h * frac / max(h, 1)))
            cv2.rectangle(mask, (x, y), (x + min(w, max(6, int(w * (frac ** 0.5)))), y + min(h, max(6, int(h * (frac ** 0.5))))), 255, -1)
        patch = rub(H, W)
        if lvl == "minor":
            patch[:] = np.array([60, 90, 170], np.float32) + rng.normal(0, 10, (H, W, 3))   # blue tarp
        post[mask > 0] = patch[mask > 0]
        # scattered debris around
        for _ in range({"minor": 3, "major": 10, "destroyed": 22}[lvl]):
            dx, dy = int(rng.normal(x + w / 2, w * 0.8)), int(rng.normal(y + h / 2, h * 0.8))
            cv2.circle(post, (dx, dy), int(rng.integers(1, 4)), (130, 115, 100), -1)

    # a road blocked by debris (ground truth for the road check)
    block_pt = (460, 430)
    for _ in range(140):
        px_, py_ = int(rng.normal(block_pt[0], 7)), int(rng.normal(block_pt[1], 10))
        cv2.circle(post, (px_, py_), int(rng.integers(2, 5)), (125, 108, 95), -1)

    # ---- imaging differences between the two passes ----------------------
    post = post * 0.95 + 8 + _noise(rng, H, W, 60)[..., None] * 3       # lighting shift
    post += rng.normal(0, 2.5, post.shape)                                # sensor noise
    A = cv2.getRotationMatrix2D((W / 2, H / 2), 0.4, 1.0); A[0, 2] += 4; A[1, 2] -= 3   # misalignment
    post = cv2.warpAffine(np.clip(post, 0, 255).astype(np.uint8), A, (W, H), borderMode=cv2.BORDER_REFLECT)
    pre = np.clip(pre, 0, 255).astype(np.uint8)

    # ---- georeferencing + vector layers ------------------------------------
    dlat = GSD / 110_574.0
    dlon = GSD / (111_320.0 * np.cos(np.radians(LAT0)))
    import rasterio
    from rasterio.transform import from_origin
    tr = from_origin(LON0, LAT0, dlon, dlat)
    for name, arr in (("pre.tif", pre), ("post.tif", post)):
        with rasterio.open(os.path.join(out_dir, name), "w", driver="GTiff", height=H, width=W, count=3,
                           dtype="uint8", crs="EPSG:4326", transform=tr) as ds:
            ds.write(arr.transpose(2, 0, 1))
    to_ll = lambda cx, cy: [LON0 + cx * dlon, LAT0 - cy * dlat]
    feats, gt_rows, pois = [], [], []
    for i, b in enumerate(blds):
        x, y, w, h = b["x"], b["y"], b["w"], b["h"]
        ring = [to_ll(x, y), to_ll(x + w, y), to_ll(x + w, y + h), to_ll(x, y + h), to_ll(x, y)]
        bid = f"B-{i + 1:03d}"
        feats.append({"type": "Feature", "properties": {"id": bid},
                      "geometry": {"type": "Polygon", "coordinates": [ring]}})
        gt_rows.append({"id": bid, "gt": b["gt"], "kind": b["kind"]})
        if b["kind"] != "house":
            nm = {"hospital": "City Hospital (demo)", "school": f"School {bid} (demo)", "shelter": "Cyclone Shelter (demo)"}[b["kind"]]
            amen = {"hospital": "hospital", "school": "school", "shelter": "shelter"}[b["kind"]]
            pois.append({"type": "Feature", "properties": {"name": nm, "amenity": amen},
                         "geometry": {"type": "Point", "coordinates": to_ll(x + w / 2, y + h / 2)}})
    json.dump({"type": "FeatureCollection", "features": feats}, open(os.path.join(out_dir, "footprints.geojson"), "w"))
    json.dump({"type": "FeatureCollection", "features": pois}, open(os.path.join(out_dir, "pois.geojson"), "w"))
    rf = [{"type": "Feature", "properties": {"id": f"R-{k + 1:02d}", "name": "demo road"},
           "geometry": {"type": "LineString", "coordinates": [to_ll(*a), to_ll(*b_)]}} for k, (a, b_) in enumerate(roads_px)]
    json.dump({"type": "FeatureCollection", "features": rf}, open(os.path.join(out_dir, "roads.geojson"), "w"))
    pd.DataFrame(gt_rows).to_csv(os.path.join(out_dir, "ground_truth.csv"), index=False)
    return out_dir


if __name__ == "__main__":
    import sys
    print("wrote", make_demo(sys.argv[1] if len(sys.argv) > 1 else "demo_data"))
