"""Step 2-3: CHANGE DETECTION + DAMAGE SCORING per building / road segment.

We do NOT ask "is there any pixel difference?" (shadows, seasons and noise
would fool us). We ask, *inside each pre-event building footprint*:
  1. changed_fraction : what share of the roof area changed a lot?
  2. dissimilarity    : did the local structure (SSIM) get destroyed?
  3. edge_change      : did the crisp roof edges vanish / turn into rubble texture?
and blend them into one 0..1 damage score. Thresholds are adjustable and can
be CALIBRATED on labelled data (see evaluate.py).
"""
from __future__ import annotations
from dataclasses import dataclass, field
import cv2
import numpy as np
from skimage.color import rgb2lab
from skimage.exposure import match_histograms
from skimage.metrics import structural_similarity

SEVERITIES = ["none", "minor", "major", "destroyed"]


@dataclass
class Config:
    minor: float = 0.22          # score >= minor      -> "minor"
    major: float = 0.42          # score >= major      -> "major"
    destroyed: float = 0.68      # score >= destroyed  -> "destroyed"
    de_changed: float = 22.0     # Lab colour distance counted as "changed pixel"
    w_frac: float = 0.55
    w_dissim: float = 0.30
    w_edge: float = 0.15
    hotspot_radius_m: float = 80.0
    poi_radius_m: float = 8.0
    road_width_m: float = 7.0
    road_seg_m: float = 25.0
    crit_weights: dict = field(default_factory=lambda: {
        "hospital": 1.0, "clinic": 0.7, "fire_station": 0.8, "school": 0.6,
        "shelter": 0.6, "police": 0.5, "college": 0.5, "place_of_worship": 0.2})


@dataclass
class Fields:
    changed: np.ndarray    # bool, per-pixel "changed a lot"
    dissim: np.ndarray     # 0..1
    edge_pre: np.ndarray
    edge_post: np.ndarray
    heat: np.ndarray       # 0..1 smooth change heat-map for display


def prepare_fields(pre: np.ndarray, post_aligned: np.ndarray, cfg: Config) -> Fields:
    # 1) make lighting comparable: match post colours to pre colours
    post_m = match_histograms(post_aligned, pre, channel_axis=-1).astype(np.uint8)
    # 2) structure similarity (lower = more changed)
    g1 = cv2.GaussianBlur(cv2.cvtColor(pre, cv2.COLOR_RGB2GRAY), (0, 0), 1.2).astype(np.float32) / 255
    g2 = cv2.GaussianBlur(cv2.cvtColor(post_m, cv2.COLOR_RGB2GRAY), (0, 0), 1.2).astype(np.float32) / 255
    _, smap = structural_similarity(g1, g2, win_size=9, data_range=1.0, full=True)
    dissim = np.clip(1 - smap, 0, 1).astype(np.float32)
    # 3) colour change in Lab space (perceptual distance)
    de = np.sqrt(((rgb2lab(pre) - rgb2lab(post_m)) ** 2).sum(-1)).astype(np.float32)
    de = cv2.GaussianBlur(de, (0, 0), 1.0)
    changed = de > cfg.de_changed
    # 4) edge strength (Sobel magnitude)
    def grad(g):
        gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3); gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
        return np.hypot(gx, gy)
    ep, eq = grad(g1), grad(g2)
    heat = np.clip(0.5 * np.clip(de / 45, 0, 1) + 0.5 * dissim, 0, 1)
    heat = cv2.GaussianBlur(heat, (0, 0), 2.0)
    return Fields(changed, dissim, ep, eq, heat)


def region_stats(f: Fields, mask: np.ndarray, cfg: Config) -> dict:
    n = int(mask.sum())
    if n == 0:
        return dict(changed_fraction=0.0, dissim=0.0, edge_change=0.0, score=0.0, area_px=0)
    frac = float(f.changed[mask].mean())
    dis = float(f.dissim[mask].mean())
    e1, e2 = float(f.edge_pre[mask].mean()), float(f.edge_post[mask].mean())
    edge = abs(e1 - e2) / (e1 + e2 + 0.05)
    score = cfg.w_frac * frac + cfg.w_dissim * min(1.0, dis * 1.6) + cfg.w_edge * min(1.0, edge * 2.0)
    return dict(changed_fraction=frac, dissim=dis, edge_change=edge, score=float(np.clip(score, 0, 1)), area_px=n)


def severity_from_score(s: float, cfg: Config) -> str:
    return "destroyed" if s >= cfg.destroyed else "major" if s >= cfg.major else "minor" if s >= cfg.minor else "none"


def poly_mask(poly_px, shape):
    """Boolean mask of a pixel-space polygon (cropped to bbox for speed)."""
    h, w = shape[:2]
    mask = np.zeros((h, w), np.uint8)
    if poly_px.geom_type == "Polygon":
        polys = [poly_px]
    else:
        polys = list(poly_px.geoms)
    for p in polys:
        pts = np.round(np.array(p.exterior.coords)).astype(np.int32)
        cv2.fillPoly(mask, [pts], 1)
    return mask.astype(bool)


def line_mask(line_px, shape, width_px):
    mask = np.zeros(shape[:2], np.uint8)
    pts = np.round(np.array(line_px.coords)).astype(np.int32)
    cv2.polylines(mask, [pts], False, 1, thickness=max(1, int(round(width_px))))
    return mask.astype(bool)


# ---------------------------------------------------------------------------
# Priority Index  (the "uniqueness": damage x importance x neighbourhood)
# ---------------------------------------------------------------------------
def criticality(geom, pois, cfg: Config):
    """Weight (0..1) + name of a critical facility that IS this building
    (POI point inside the footprint, or within poi_radius_m of its edge).
    Neighbouring houses must NOT inherit a hospital's importance."""
    from shapely.geometry import Point
    from .geo import M_PER_DEG_LAT
    best_w, best_n = 0.0, ""
    tol_deg = cfg.poi_radius_m / M_PER_DEG_LAT
    for p in pois:
        if geom.distance(Point(p["lon"], p["lat"])) <= tol_deg:
            w = cfg.crit_weights.get(p["amenity"], 0.3)
            if w > best_w:
                best_w, best_n = w, p["name"] or p["amenity"]
    return best_w, best_n


def hotspot_fraction(lons, lats, severe, cfg: Config):
    """Share of neighbours (within radius) that are severely damaged -> clusters first."""
    from scipy.spatial import cKDTree
    lat0 = float(np.mean(lats))
    from .geo import M_PER_DEG_LAT, m_per_deg_lon
    xy = np.c_[(np.asarray(lons)) * m_per_deg_lon(lat0), np.asarray(lats) * M_PER_DEG_LAT]
    tree = cKDTree(xy)
    out = np.zeros(len(xy))
    for i, nb in enumerate(tree.query_ball_point(xy, cfg.hotspot_radius_m)):
        nb = [j for j in nb if j != i]
        out[i] = (sum(bool(severe[j]) for j in nb) / len(nb)) if nb else 0.0
    return out


def tier_for(severity: str, crit_w: float, hotspot: float) -> str:
    if severity == "destroyed":
        return "P1"
    if severity == "major":
        return "P1" if (crit_w > 0 or hotspot >= 0.3) else "P2"
    if severity == "minor":
        return "P2" if crit_w > 0 else "P3"
    return "P4"


def priority_index(score: float, crit_w: float, hotspot: float) -> float:
    """Transparent formula (show it to the judges):
         PI = 100 x damage_score x (1 + criticality) x (1 + 0.5 x hotspot)"""
    return round(100.0 * score * (1.0 + crit_w) * (1.0 + 0.5 * hotspot), 1)


def confidence_label(score: float, cfg: Config, align_quality: str) -> str:
    """Heuristic (NOT a calibrated probability): distance to nearest severity threshold."""
    margin = min(abs(score - t) for t in (cfg.minor, cfg.major, cfg.destroyed))
    base = 2 if margin > 0.08 else 1 if margin > 0.03 else 0
    if align_quality == "weak": base -= 1
    return ["Low", "Medium", "High"][max(0, min(2, base))]
