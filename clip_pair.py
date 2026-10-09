"""Clip BIG pre/post GeoTIFFs (e.g. Vantor/Maxar Open Data) to the same small area on the SAME pixel grid.

  python clip_pair.py --pre big_pre.tif --post big_post.tif --bbox 82.2500 16.9854 82.2546 16.9900 --gsd 0.5 --out real_pair

bbox = west south east north in lon/lat. Writes real_pair/pre.tif and real_pair/post.tif ready for the app.
Keep the area ~1000x1000 to 3000x3000 pixels (about 0.5-1.5 km at 0.5 m) so analysis stays fast.
Sources can be local files or remote COG URLs (rasterio streams only the needed window).
"""
import argparse, os
import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import reproject, Resampling


def clip_to_grid(src_path, bbox, gsd_m, out_path):
    w, s, e, n = bbox
    lat0 = (s + n) / 2
    dlat = gsd_m / 110_574.0
    dlon = gsd_m / (111_320.0 * np.cos(np.radians(lat0)))
    W, H = int(round((e - w) / dlon)), int(round((n - s) / dlat))
    if W * H > 36_000_000: raise ValueError("Area too large; shrink bbox or raise --gsd")
    dst_tr = from_origin(w, n, dlon, dlat)
    out = np.zeros((3, H, W), np.uint8)
    with rasterio.open(src_path) as src:
        for b in range(3):
            reproject(source=rasterio.band(src, min(b + 1, src.count)), destination=out[b], dst_transform=dst_tr,
                      dst_crs="EPSG:4326", resampling=Resampling.bilinear)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with rasterio.open(out_path, "w", driver="GTiff", height=H, width=W, count=3, dtype="uint8", crs="EPSG:4326", transform=dst_tr) as dst:
        dst.write(out)
    return out_path, (H, W)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pre", required=True); ap.add_argument("--post", required=True)
    ap.add_argument("--bbox", nargs=4, type=float, required=True); ap.add_argument("--gsd", type=float, default=0.5)
    ap.add_argument("--out", default="real_pair")
    a = ap.parse_args()
    for name, p in (("pre", a.pre), ("post", a.post)):
        print(name, clip_to_grid(p, a.bbox, a.gsd, os.path.join(a.out, f"{name}.tif")))
