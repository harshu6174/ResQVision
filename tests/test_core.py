import json, numpy as np, pandas as pd, pytest, os
from shapely.geometry import Polygon
from damage_analyzer.geo import GeoRef, haversine_m, poly_to_px
from damage_analyzer.scoring import Config, severity_from_score, tier_for, priority_index, criticality
from damage_analyzer.routing import plan_route
from damage_analyzer.io_utils import load_image, load_footprints, load_pois, load_roads, load_xbd_pair, grid_zones
from damage_analyzer.pipeline import analyze
from damage_analyzer.evaluate import report, top_k_precision
import fetch_osm

D = os.path.join(os.path.dirname(__file__), "..", "demo_data") + "/"


def test_georef_roundtrip_and_scale():
    g = GeoRef.from_bounds(82.0, 16.0, 82.01, 16.01, (1000, 1000))
    lon, lat = g.px_to_lonlat(500, 500)
    c, r = g.lonlat_to_px(lon, lat)
    assert abs(c - 500) < 1e-6 and abs(r - 500) < 1e-6
    assert 0.9 < g.metres_per_px < 1.2                      # ~1.1 km / 1000 px


def test_haversine_known_distance():
    assert abs(float(haversine_m(0, 0, 0, 1)) - 111_195) < 300      # 1 degree latitude


def test_severity_and_priority_logic():
    c = Config()
    assert [severity_from_score(s, c) for s in (0.05, 0.3, 0.5, 0.8)] == ["none", "minor", "major", "destroyed"]
    assert tier_for("minor", 1.0, 0) == "P2" and tier_for("minor", 0, 0) == "P3" and tier_for("destroyed", 0, 0) == "P1"
    assert priority_index(0.5, 1.0, 0.0) > priority_index(0.5, 0.0, 0.0) > 0
    assert priority_index(0.5, 0.0, 1.0) > priority_index(0.5, 0.0, 0.0)


def test_neighbour_does_not_inherit_facility():
    hosp = Polygon([(0, 0), (0.0002, 0), (0.0002, 0.0002), (0, 0.0002)])
    house = Polygon([(0.0004, 0), (0.0005, 0), (0.0005, 0.0001), (0.0004, 0.0001)])   # ~20 m away
    pois = [{"name": "H", "amenity": "hospital", "lon": 0.0001, "lat": 0.0001}]
    assert criticality(hosp, pois, Config())[0] == 1.0
    assert criticality(house, pois, Config())[0] == 0.0


def test_route_starts_most_urgent_and_is_geo():
    df = pd.DataFrame({"lon": [82.0, 82.001, 82.0005], "lat": [16.0, 16.001, 16.0], "priority_index": [10, 99, 50],
                       "id": list("abc")})
    r, km, url = plan_route(df, (82.0, 16.0), 3)
    assert r.id[0] == "b" and km > 0 and url.startswith("https://www.google.com/maps/dir/")


@pytest.fixture(scope="module")
def demo_result():
    pre, g = load_image(D + "pre.tif"); post, _ = load_image(D + "post.tif")
    return analyze(pre, post, g, load_footprints(D + "footprints.geojson"), load_pois(D + "pois.geojson"), load_roads(D + "roads.geojson"))


def test_alignment_recovers_injected_misalignment(demo_result):
    ai = demo_result.align_info
    assert ai["quality"] == "good" and abs(abs(ai["rotation_deg"]) - 0.4) < 0.1


def test_pipeline_accuracy_on_synthetic(demo_result):
    rep, m = report(demo_result, pd.read_csv(D + "ground_truth.csv"))
    assert rep["accuracy"] > 0.8 and rep["damaged_vs_not"]["f1"] > 0.88 and rep["top10_precision"] >= 0.8


def test_geo_output_inside_scene_and_blocked_road_found(demo_result):
    df = demo_result.df
    assert df.lat.between(16.98, 16.99).all() and df.lon.between(82.25, 82.26).all()
    roads = df[(df.type == "road") & (df.severity != "none")]
    assert len(roads) >= 1 and abs(roads.lon.iloc[0] - 82.25216) < 0.0002          # debris placed at x=460px


def test_hospital_outranks_equally_damaged_house(demo_result):
    b = demo_result.df
    assert (b[b.facility.str.contains("Hospital", na=False)].priority_index.iloc[0] >
            b[(b.severity == "minor") & (b.facility == "")].priority_index.max())


def test_grid_zone_fallback_runs():
    pre, g = load_image(D + "pre.tif"); post, _ = load_image(D + "post.tif")
    res = analyze(pre, post, g, grid_zones(g, pre.shape, 30))
    assert (res.df.type == "zone").all() and len(res.df) > 100


def test_xbd_parser_with_fabricated_schema(tmp_path):
    import cv2
    img = np.random.default_rng(0).integers(0, 255, (256, 256, 3), dtype=np.uint8)
    cv2.imwrite(str(tmp_path / "pre.png"), img); cv2.imwrite(str(tmp_path / "post.png"), img)
    def poly(x, y, s=20): return [(x, y), (x + s, y), (x + s, y + s), (x, y + s), (x, y)]
    def ll(x, y): return (82 + x * 5e-6, 17 - y * 5e-6)
    feats_xy, feats_ll = [], []
    for i, (x, y) in enumerate([(10, 10), (80, 40), (150, 120), (30, 200)]):
        w = lambda pts: "POLYGON ((" + ", ".join(f"{a} {b}" for a, b in pts) + "))"
        feats_xy.append({"properties": {"feature_type": "building", "uid": f"u{i}"}, "wkt": w(poly(x, y))})
        feats_ll.append({"properties": {"feature_type": "building", "uid": f"u{i}"}, "wkt": w([ll(*p) for p in poly(x, y)])})
    pre_j = {"features": {"xy": feats_xy, "lng_lat": feats_ll}}
    post_j = {"features": {"xy": feats_xy, "lng_lat": [{**f, "properties": {**f["properties"], "subtype": "destroyed"}} for f in feats_ll]}}
    json.dump(pre_j, open(tmp_path / "pre.json", "w")); json.dump(post_j, open(tmp_path / "post.json", "w"))
    pre, post, g, foot, gt = load_xbd_pair(tmp_path / "pre.png", tmp_path / "post.png", tmp_path / "pre.json", tmp_path / "post.json")
    assert len(foot) == 4 and gt["u0"] == "destroyed"
    lon, lat = g.px_to_lonlat(10, 10); assert abs(lon - 82.00005) < 1e-6 and abs(lat - (17 - 5e-5)) < 1e-6


def test_osm_parser_offline():
    data = {"elements": [
        {"type": "way", "id": 1, "tags": {"building": "yes"}, "geometry": [{"lon": 0, "lat": 0}, {"lon": 1, "lat": 0}, {"lon": 1, "lat": 1}, {"lon": 0, "lat": 1}]},
        {"type": "way", "id": 2, "tags": {"highway": "residential", "name": "X"}, "geometry": [{"lon": 0, "lat": 0}, {"lon": 1, "lat": 1}]},
        {"type": "node", "id": 3, "lon": 0.5, "lat": 0.5, "tags": {"amenity": "hospital", "name": "H"}}]}
    f, p, r = fetch_osm.parse_overpass(data)
    assert len(f["features"]) == 1 and len(p["features"]) == 1 and len(r["features"]) == 1
    assert f["features"][0]["geometry"]["coordinates"][0][0] == f["features"][0]["geometry"]["coordinates"][0][-1]
