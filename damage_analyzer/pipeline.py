"""The whole analysis in one function: images + layers  ->  geo-located, prioritised damage list."""
from __future__ import annotations
from dataclasses import dataclass, field
import cv2
import numpy as np
import pandas as pd
from shapely.geometry import LineString
from .align import align_post_to_pre
from .geo import GeoRef, poly_to_px, maps_link, m_per_deg_lon, M_PER_DEG_LAT
from .scoring import (Config, prepare_fields, region_stats, severity_from_score, poly_mask, line_mask,
                      criticality, hotspot_fraction, tier_for, priority_index, confidence_label)
from .routing import plan_route

SEV_COLORS = {"destroyed": (214, 40, 40), "major": (255, 140, 0), "minor": (255, 213, 74), "none": (60, 170, 90)}


@dataclass
class Result:
    df: pd.DataFrame
    pre: np.ndarray
    post: np.ndarray               # aligned post image
    fields: object
    georef: GeoRef
    align_info: dict
    warnings: list
    cfg: Config
    geoms: dict = field(default_factory=dict)
    summary: dict = field(default_factory=dict)


def analyze(pre, post, georef: GeoRef, footprints, pois=None, roads=None, cfg: Config | None = None, progress=None):
    cfg = cfg or Config(); pois = pois or []; roads = roads or []
    say = progress or (lambda *_: None)
    warnings = []
    say("Aligning images…")
    post_a, ainfo = align_post_to_pre(pre, post)
    if ainfo["quality"] == "failed":
        warnings.append("Image alignment FAILED – results are unreliable. Use a better-matched pair.")
    elif ainfo["quality"] == "weak":
        warnings.append("Image alignment is weak – confidence values were lowered.")
    bright = float(((post_a.min(-1) > 215)).mean())
    if bright > 0.25:
        warnings.append(f"{bright:.0%} of the post image is very bright/white – possible cloud cover.")
    say("Detecting changes…")
    f = prepare_fields(pre, post_a, cfg)
    mpp = georef.metres_per_px
    rows = []; geoms = {}
    say("Scoring buildings…")
    for fp in footprints:
        pg = poly_to_px(fp["geom"], georef)
        if pg.is_empty or not pg.is_valid: pg = pg.buffer(0)
        mask = poly_mask(pg, pre.shape)
        st = region_stats(f, mask, cfg)
        if st["area_px"] < 6: continue
        c = fp["geom"].centroid
        geoms[fp["id"]] = fp["geom"]
        rows.append(dict(id=fp["id"], type=fp.get("type", "building"), lon=c.x, lat=c.y, area_m2=round(st["area_px"] * mpp * mpp, 1),
                         damage_score=round(st["score"], 3), changed_fraction=round(st["changed_fraction"], 3),
                         dissimilarity=round(st["dissim"], 3), edge_change=round(st["edge_change"], 3)))
    df = pd.DataFrame(rows)
    if df.empty:
        return Result(df, pre, post_a, f, georef, ainfo, warnings + ["No usable building footprints."], cfg, {}, {})
    df["severity"] = [severity_from_score(s, cfg) for s in df.damage_score]
    crit = [criticality(geoms[r.id], pois, cfg) for r in df.itertuples()]
    df["criticality"] = [c[0] for c in crit]; df["facility"] = [c[1] for c in crit]
    df["hotspot"] = np.round(hotspot_fraction(df.lon.values, df.lat.values,
                                             df.severity.isin(["major", "destroyed"]).values, cfg), 2)
    df["tier"] = [tier_for(r.severity, r.criticality, r.hotspot) for r in df.itertuples()]
    df["priority_index"] = [priority_index(r.damage_score, r.criticality, r.hotspot) for r in df.itertuples()]
    df["confidence"] = [confidence_label(r.damage_score, cfg, ainfo["quality"]) for r in df.itertuples()]
    # ---- roads: split into short segments, flag likely obstruction ----------
    segs = []
    say("Checking roads…")
    seg_m = cfg.road_seg_m
    for rd in roads:
        g = rd["geom"]
        lat0 = g.centroid.y
        L_m = g.length * np.hypot(m_per_deg_lon(lat0), M_PER_DEG_LAT) / np.sqrt(2)   # rough length
        n = max(1, int(L_m // seg_m))
        for k in range(n):
            a, b = g.interpolate(k / n, normalized=True), g.interpolate((k + 1) / n, normalized=True)
            seg = LineString([a, b]); sp = poly_to_px(seg, georef)
            m = line_mask(sp, pre.shape, cfg.road_width_m / mpp)
            st = region_stats(f, m, cfg)
            if st["area_px"] < 6: continue
            mid = seg.interpolate(0.5, normalized=True)
            sev = "major" if st["score"] >= cfg.major else "minor" if st["score"] >= cfg.minor else "none"
            segs.append(dict(id=f"{rd['id']}-{k + 1:02d}", type="road", lon=mid.x, lat=mid.y, area_m2=0.0,
                             damage_score=round(st["score"], 3), changed_fraction=round(st["changed_fraction"], 3),
                             dissimilarity=round(st["dissim"], 3), edge_change=round(st["edge_change"], 3),
                             severity=sev, criticality=0.0, facility="", hotspot=0.0,
                             tier="P2" if sev == "major" else "P3" if sev == "minor" else "P4",
                             priority_index=round(100 * st["score"] * 0.8, 1),
                             confidence=confidence_label(st["score"], cfg, ainfo["quality"]))); geoms[segs[-1]["id"]] = seg
    if segs:
        df = pd.concat([df, pd.DataFrame(segs)], ignore_index=True)
    df["maps_link"] = [maps_link(r.lat, r.lon) for r in df.itertuples()]
    df = df.sort_values(["priority_index"], ascending=False).reset_index(drop=True)
    df.insert(0, "rank", range(1, len(df) + 1))
    b = df[df.type != "road"]
    summary = dict(
        buildings=len(b), destroyed=int((b.severity == "destroyed").sum()), major=int((b.severity == "major").sum()),
        minor=int((b.severity == "minor").sum()), none=int((b.severity == "none").sum()),
        road_flags=int(((df.type == "road") & (df.severity != "none")).sum()),
        p1=int((df.tier == "P1").sum()), critical_facilities_hit=int(((b.criticality > 0) & (b.severity != "none")).sum()))
    return Result(df, pre, post_a, f, georef, ainfo, warnings, cfg, geoms, summary)


def render_overlay(res: Result, top_n: int = 10, alpha: float = 0.55) -> np.ndarray:
    """Post image with colour-coded footprints and rank numbers for the top-N sites."""
    img = res.post.copy(); layer = img.copy()
    for r in res.df.itertuples():
        pg = poly_to_px(res.geoms[r.id], res.georef)
        col = SEV_COLORS[r.severity]
        if r.type != "road":
            pts = np.round(np.array(pg.exterior.coords)).astype(np.int32)
            cv2.fillPoly(layer, [pts], col)
            cv2.polylines(img, [pts], True, col, 2 if r.severity != "none" else 1)
        elif r.severity != "none":
            pts = np.round(np.array(pg.coords)).astype(np.int32)
            cv2.polylines(layer, [pts], False, (170, 40, 220), 6)
    out = cv2.addWeighted(layer, alpha, img, 1 - alpha, 0)
    for r in res.df.head(top_n).itertuples():
        cx, cy = res.georef.lonlat_to_px(r.lon, r.lat)
        cx, cy = int(cx), int(cy)
        cv2.circle(out, (cx, cy), 11, (0, 0, 0), -1); cv2.circle(out, (cx, cy), 11, (255, 255, 255), 2)
        cv2.putText(out, str(r.rank), (cx - (5 if r.rank < 10 else 10), cy + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return out
