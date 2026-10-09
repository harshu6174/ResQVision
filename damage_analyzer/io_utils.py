"""Loading images / vector layers / xBD pairs."""
from __future__ import annotations
import json
import cv2
import numpy as np
from shapely.geometry import shape
from .geo import GeoRef


def load_image(path):
    """Returns (rgb uint8 HxWx3, GeoRef or None). GeoTIFFs give a GeoRef; PNG/JPG do not."""
    p = str(path).lower()
    if p.endswith((".tif", ".tiff")):
        import rasterio
        with rasterio.open(path) as ds:
            arr = ds.read([1, 2, 3] if ds.count >= 3 else [1, 1, 1]).transpose(1, 2, 0)
        if arr.dtype != np.uint8:                      # stretch 16-bit imagery to 8-bit
            lo, hi = np.percentile(arr, (2, 98)); arr = np.clip((arr - lo) / (hi - lo + 1e-6) * 255, 0, 255).astype(np.uint8)
        try: geo = GeoRef.from_raster(path)
        except Exception: geo = None
        return arr, geo
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None: raise ValueError(f"Cannot read image {path}")
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), None


def load_footprints(path_or_obj):
    d = path_or_obj if isinstance(path_or_obj, dict) else json.load(open(path_or_obj))
    out = []
    for i, f in enumerate(d["features"]):
        g = shape(f["geometry"])
        if g.geom_type in ("Polygon", "MultiPolygon"):
            out.append({"id": str(f.get("properties", {}).get("id", f"B-{i + 1:03d}")), "geom": g})
    return out


def load_pois(path_or_obj):
    d = path_or_obj if isinstance(path_or_obj, dict) else json.load(open(path_or_obj))
    out = []
    for f in d["features"]:
        g = shape(f["geometry"]); c = g.centroid; pr = f.get("properties", {})
        out.append({"name": pr.get("name", ""), "amenity": pr.get("amenity", "other"), "lon": c.x, "lat": c.y})
    return out


def load_roads(path_or_obj):
    d = path_or_obj if isinstance(path_or_obj, dict) else json.load(open(path_or_obj))
    out = []
    for i, f in enumerate(d["features"]):
        g = shape(f["geometry"])
        if g.geom_type == "LineString":
            out.append({"id": str(f.get("properties", {}).get("id", f"R-{i + 1:02d}")),
                        "name": f.get("properties", {}).get("name", ""), "geom": g})
    return out


def load_xbd_pair(pre_png, post_png, pre_json, post_json=None):
    """Convert one xBD tile (pre+post PNG + label JSON) into our format.

    xBD JSON (as I understand the published schema): features.lng_lat[] and features.xy[]
    each hold {properties:{feature_type, uid, subtype?}, wkt:'POLYGON ((...))'}.
    xBD PNGs are NOT geo-tagged, so we FIT the pixel->lon/lat mapping from the polygons
    themselves (they exist in both coordinate systems).
    Returns pre, post, georef, footprints, ground_truth(dict uid->subtype or {}).
    """
    from shapely import wkt
    pre, _ = load_image(pre_png); post, _ = load_image(post_png)
    j = json.load(open(pre_json))
    ll = {f["properties"]["uid"]: wkt.loads(f["wkt"]) for f in j["features"]["lng_lat"]}
    xy = {f["properties"]["uid"]: wkt.loads(f["wkt"]) for f in j["features"]["xy"]}
    px_pts, ll_pts = [], []
    for uid in ll.keys() & xy.keys():
        a, b = np.array(xy[uid].exterior.coords), np.array(ll[uid].exterior.coords)
        if len(a) == len(b): px_pts += a.tolist(); ll_pts += b.tolist()
    if len(px_pts) < 6: raise ValueError("Not enough matching polygons to fit georeferencing")
    georef = GeoRef.from_gcps(px_pts, ll_pts, pre.shape[:2])
    foot = [{"id": uid, "geom": g} for uid, g in ll.items() if g.geom_type == "Polygon"]
    gt = {}
    if post_json:
        for f in json.load(open(post_json))["features"]["lng_lat"]:
            gt[f["properties"]["uid"]] = f["properties"].get("subtype", "unknown")
    return pre, post, georef, foot, gt


def grid_zones(georef, shape, cell_m=24.0):
    """FALLBACK when you have no building footprints: tile the image into square zones.
    Each zone is scored like a building, so you still get GPS-located hot spots."""
    from shapely.geometry import box
    cell_px = max(8, int(round(cell_m / georef.metres_per_px)))
    h, w = shape[:2]; out = []
    for r0 in range(0, h - cell_px + 1, cell_px):
        for c0 in range(0, w - cell_px + 1, cell_px):
            xs, ys = georef.px_to_lonlat([c0, c0 + cell_px], [r0, r0 + cell_px])
            out.append({"id": f"Z-{r0 // cell_px:02d}-{c0 // cell_px:02d}", "type": "zone",
                        "geom": box(min(xs), min(ys), max(xs), max(ys))})
    return out
