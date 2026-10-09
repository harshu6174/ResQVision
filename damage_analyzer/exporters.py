"""Outputs a field team can actually use: CSV, GeoJSON, KML (Google Earth / phones), web map, HTML report."""
from __future__ import annotations
import base64, html, json
from datetime import datetime
import cv2
import numpy as np
from .pipeline import render_overlay, SEV_COLORS
from .routing import plan_route

COLS = ["rank", "id", "type", "tier", "severity", "priority_index", "damage_score", "confidence", "lat", "lon",
        "facility", "hotspot", "area_m2", "maps_link"]
TIER_NAME = {"P1": "URGENT", "P2": "HIGH", "P3": "ROUTINE", "P4": "NO ACTION"}


def listing(res, include_none=False):
    d = res.df if include_none else res.df[res.df.severity != "none"]
    return d[COLS].copy()


def to_csv(res, include_none=False) -> str:
    return listing(res, include_none).to_csv(index=False)


def to_geojson(res, include_none=True) -> str:
    feats = []
    for r in res.df.itertuples():
        if r.severity == "none" and not include_none: continue
        g = res.geoms[r.id]
        feats.append({"type": "Feature", "geometry": json.loads(json.dumps(g.__geo_interface__)),
                      "properties": {k: getattr(r, k) for k in ("rank", "id", "type", "tier", "severity", "priority_index",
                                                                 "damage_score", "confidence", "lat", "lon", "facility", "maps_link")}})
    return json.dumps({"type": "FeatureCollection", "features": feats})


def to_kml(res) -> str:
    kcol = {"destroyed": "ff2828d6", "major": "ff008cff", "minor": "ff4ad5ff"}   # aabbggrr
    styles = "".join(f'<Style id="{k}"><IconStyle><color>{v}</color><scale>1.2</scale>'
                     f'<Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon></IconStyle></Style>'
                     for k, v in kcol.items())
    pm = []
    for r in res.df[res.df.severity != "none"].itertuples():
        desc = html.escape(f"{TIER_NAME[r.tier]} | {r.severity} | PI {r.priority_index} | confidence {r.confidence}"
                           + (f" | near {r.facility}" if r.facility else ""))
        pm.append(f'<Placemark><name>#{r.rank} {html.escape(r.id)}</name><description>{desc}</description>'
                  f'<styleUrl>#{r.severity if r.severity in kcol else "minor"}</styleUrl>'
                  f'<Point><coordinates>{r.lon:.7f},{r.lat:.7f},0</coordinates></Point></Placemark>')
    return ('<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            f'<name>Damage priority list</name>{styles}{"".join(pm)}</Document></kml>')


def to_web_map(res, top_n=40) -> str:
    """Interactive map (Esri satellite tiles load in the user's browser)."""
    import folium
    lat0, lon0 = res.df.lat.mean(), res.df.lon.mean()
    m = folium.Map(location=[lat0, lon0], zoom_start=17, tiles=None, max_zoom=19)
    folium.TileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                     attr="Esri World Imagery", name="Satellite", max_zoom=19).add_to(m)
    folium.TileLayer("OpenStreetMap", name="Street map").add_to(m)
    col = {"destroyed": "#D62828", "major": "#FF8C00", "minor": "#FFD54A", "none": "#3CAA5A"}
    for r in res.df.itertuples():
        if r.severity == "none": continue
        g = res.geoms[r.id]
        pop = (f"<b>#{r.rank} {html.escape(r.id)}</b> ({html.escape(r.type)})<br>{TIER_NAME[r.tier]} · {r.severity}<br>"
               f"Priority Index {r.priority_index} · confidence {r.confidence}<br>"
               f"{r.lat:.6f}, {r.lon:.6f}<br>" + (f"Near: {html.escape(r.facility)}<br>" if r.facility else "") +
               f'<a href="{r.maps_link}" target="_blank">Open in Google Maps</a>')
        folium.GeoJson(g.__geo_interface__, style_function=lambda _, c=col[r.severity]: {"color": c, "weight": 2, "fillColor": c, "fillOpacity": 0.5},
                       popup=folium.Popup(pop, max_width=260)).add_to(m)
        if r.rank <= top_n and r.type != "road":
            folium.Marker([r.lat, r.lon], icon=folium.DivIcon(html=f'<div style="background:#000;color:#fff;border:2px solid #fff;'
                          f'border-radius:50%;width:22px;height:22px;text-align:center;font:700 12px/20px sans-serif">{r.rank}</div>')).add_to(m)
    folium.LayerControl().add_to(m)
    return m.get_root().render()


def _b64(img_rgb) -> str:
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf).decode()


def to_report_html(res, title="Satellite Damage Assessment", base_lonlat=None) -> str:
    s = res.summary; top = listing(res).head(15)
    route, km, url = plan_route(res.df, base_lonlat, 8)
    rows = "".join(
        f"<tr><td>{r.rank}</td><td>{html.escape(r.id)}</td><td class='t{r.tier}'>{TIER_NAME[r.tier]}</td><td>{r.severity}</td>"
        f"<td>{r.priority_index}</td><td>{r.lat:.6f}, {r.lon:.6f}</td><td>{html.escape(r.facility or '-')}</td>"
        f"<td><a href='{r.maps_link}'>map</a></td></tr>" for r in top.itertuples())
    warn = "".join(f"<li>{html.escape(w)}</li>" for w in res.warnings) or "<li>None</li>"
    return f"""<!doctype html><html><head><meta charset=utf-8><title>{title}</title><style>
body{{font-family:Inter,Arial,sans-serif;max-width:900px;margin:20px auto;color:#1a1a1a}}h1{{margin:0}}
.k{{display:flex;gap:10px;margin:12px 0}}.k div{{flex:1;border:1px solid #ccc;border-radius:8px;padding:8px;text-align:center}}
.k b{{display:block;font-size:22px}}table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{border:1px solid #ccc;padding:4px 6px}}
th{{background:#eee}}.tP1{{background:#fde3e3;font-weight:700}}.tP2{{background:#ffebd1}}.tP3{{background:#fff4cc}}img{{width:100%;border:1px solid #999}}
.note{{background:#f6f6f6;border-left:4px solid #888;padding:6px 10px;font-size:13px}}</style></head><body>
<h1>{title}</h1><p>Generated {datetime.now():%Y-%m-%d %H:%M} · automated screening, <b>not</b> a structural-safety certificate.</p>
<div class=k><div><b>{s['buildings']}</b>buildings analysed</div><div><b style='color:#D62828'>{s['destroyed']}</b>destroyed</div>
<div><b style='color:#FF8C00'>{s['major']}</b>major</div><div><b style='color:#b88a00'>{s['minor']}</b>minor</div>
<div><b>{s['road_flags']}</b>road segments flagged</div><div><b>{s['critical_facilities_hit']}</b>critical facilities affected</div></div>
<h2>Damage map (top 10 numbered)</h2><img src="data:image/jpeg;base64,{_b64(render_overlay(res))}">
<h2>Inspect-first list (top 15)</h2><table><tr><th>#</th><th>ID</th><th>Priority</th><th>Damage</th><th>PI</th><th>GPS (lat, lon)</th><th>Near</th><th>Link</th></tr>{rows}</table>
<h2>Suggested field route (top 8, {km} km)</h2><p><a href="{url}">Open route in Google Maps</a></p>
<h2>Quality warnings</h2><ul>{warn}</ul>
<p class=note>Priority Index = 100 × damage score × (1 + facility criticality) × (1 + 0.5 × neighbourhood damage share). Satellite imagery shows
visible damage only; low resolution, cloud and shadows reduce accuracy. Every flagged site needs on-site verification.</p></body></html>"""
