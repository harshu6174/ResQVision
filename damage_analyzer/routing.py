"""Field inspection route: visit the most urgent site first, then hop to the nearest next."""
from __future__ import annotations
import numpy as np
from .geo import haversine_m


def plan_route(df, base_lonlat=None, k: int = 8):
    """df needs columns lon, lat, priority_index. Returns (ordered_df, total_km, google_maps_url)."""
    top = df.sort_values("priority_index", ascending=False).head(k).reset_index(drop=True)
    if top.empty:
        return top, 0.0, ""
    remaining = list(range(len(top)))
    order = [remaining.pop(0)]                         # most urgent first
    while remaining:
        cur = order[-1]
        d = [float(haversine_m(top.lon[cur], top.lat[cur], top.lon[j], top.lat[j])) for j in remaining]
        order.append(remaining.pop(int(np.argmin(d))))
    ordered = top.loc[order].reset_index(drop=True)
    ordered.insert(0, "visit_order", range(1, len(ordered) + 1))
    pts = [(r.lat, r.lon) for r in ordered.itertuples()]
    total = 0.0
    seq = ([(base_lonlat[1], base_lonlat[0])] if base_lonlat else []) + pts
    for a, b in zip(seq[:-1], seq[1:]):
        total += float(haversine_m(a[1], a[0], b[1], b[0]))
    origin = f"{seq[0][0]:.6f},{seq[0][1]:.6f}"
    dest = f"{seq[-1][0]:.6f},{seq[-1][1]:.6f}"
    wps = "|".join(f"{a:.6f},{b:.6f}" for a, b in seq[1:-1])
    url = f"https://www.google.com/maps/dir/?api=1&origin={origin}&destination={dest}&travelmode=driving"
    if wps: url += f"&waypoints={wps}"
    return ordered, round(total / 1000, 2), url
