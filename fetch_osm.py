"""Fetch pre-disaster building footprints, key facilities and roads from OpenStreetMap (Overpass API).
Run this on YOUR laptop (needs internet):

  python fetch_osm.py --bbox 82.250 16.985 82.256 16.990 --out osm_data

bbox = west south east north (lon/lat). Creates footprints.geojson, pois.geojson, roads.geojson
that the app/pipeline read directly.  NOTE: parsing is unit-tested offline; the live call was not run in the build sandbox.
"""
import argparse, json, os
import requests

URL = "https://overpass-api.de/api/interpreter"


def build_query(w, s, e, n):
    bb = f"{s},{w},{n},{e}"
    return f"""[out:json][timeout:60];
(way["building"]({bb}); way["highway"~"^(motorway|trunk|primary|secondary|tertiary|residential|unclassified|service)$"]({bb});
 node["amenity"~"hospital|clinic|school|college|fire_station|police|shelter|place_of_worship"]({bb});
 way["amenity"~"hospital|clinic|school|college|fire_station|police|shelter"]({bb}););
out geom;"""


def parse_overpass(data):
    foot, pois, roads = [], [], []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if el["type"] == "node" and "amenity" in tags:
            pois.append({"type": "Feature", "properties": {"name": tags.get("name", ""), "amenity": tags["amenity"]},
                         "geometry": {"type": "Point", "coordinates": [el["lon"], el["lat"]]}})
        elif el["type"] == "way" and "geometry" in el:
            ring = [[p["lon"], p["lat"]] for p in el["geometry"]]
            if "building" in tags and len(ring) >= 4:
                if ring[0] != ring[-1]: ring.append(ring[0])
                foot.append({"type": "Feature", "properties": {"id": f"OSM-{el['id']}"},
                             "geometry": {"type": "Polygon", "coordinates": [ring]}})
                if "amenity" in tags:     # a building that is itself a hospital/school
                    cx = sum(p[0] for p in ring) / len(ring); cy = sum(p[1] for p in ring) / len(ring)
                    pois.append({"type": "Feature", "properties": {"name": tags.get("name", ""), "amenity": tags["amenity"]},
                                 "geometry": {"type": "Point", "coordinates": [cx, cy]}})
            elif "highway" in tags and len(ring) >= 2:
                roads.append({"type": "Feature", "properties": {"id": f"OSM-{el['id']}", "name": tags.get("name", "")},
                              "geometry": {"type": "LineString", "coordinates": ring}})
    fc = lambda f: {"type": "FeatureCollection", "features": f}
    return fc(foot), fc(pois), fc(roads)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--bbox", nargs=4, type=float, required=True); ap.add_argument("--out", default="osm_data")
    a = ap.parse_args(); os.makedirs(a.out, exist_ok=True)
    r = requests.post(URL, data={"data": build_query(*a.bbox)}, timeout=120); r.raise_for_status()
    f, p, rd = parse_overpass(r.json())
    for name, obj in (("footprints", f), ("pois", p), ("roads", rd)):
        json.dump(obj, open(os.path.join(a.out, f"{name}.geojson"), "w"))
    print(f"buildings={len(f['features'])} facilities={len(p['features'])} roads={len(rd['features'])} -> {a.out}/")
