"""Tests for the web backend (server.py). Run:  python -m pytest -q tests/test_api.py"""
import os, sys, time
import pytest

pytest.importorskip("fastapi")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fastapi.testclient import TestClient   # noqa: E402
import server                                # noqa: E402

client = TestClient(server.app)


@pytest.fixture(scope="module")
def run_id():
    r = client.post("/api/runs", data={"source": "demo"})
    assert r.status_code == 202
    rid = r.json()["run_id"]
    for _ in range(120):                     # wait up to ~60 s for the background job
        st = client.get(f"/api/runs/{rid}").json()
        if st["status"] != "running":
            break
        time.sleep(0.5)
    assert st["status"] == "done", st
    return rid


def test_page_is_served():
    r = client.get("/")
    assert r.status_code == 200 and "Damage Analyzer" in r.text


def test_status_has_summary(run_id):
    st = client.get(f"/api/runs/{run_id}").json()
    assert st["summary"]["buildings"] > 50 and st["image"]["w"] > 0 and st["demo"] is True


def test_sites_are_ranked_and_complete(run_id):
    sites = client.get(f"/api/runs/{run_id}/sites").json()["sites"]
    assert sites and sites[0]["rank"] == 1
    assert all(s["severity"] != "none" for s in sites)
    assert sites == sorted(sites, key=lambda s: -s["priority_index"])
    assert {"px", "tier_name", "maps_link", "criticality"} <= set(sites[0])


@pytest.mark.parametrize("kind", ["pre", "post", "overlay", "heat"])
def test_images_are_jpeg(run_id, kind):
    r = client.get(f"/api/runs/{run_id}/images/{kind}.jpg")
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"


def test_crop_and_bad_requests(run_id):
    assert client.get(f"/api/runs/{run_id}/sites/1/crop/post.jpg").status_code == 200
    assert client.get(f"/api/runs/{run_id}/sites/99999/crop/post.jpg").status_code == 404
    assert client.get(f"/api/runs/{run_id}/images/nope.jpg").status_code == 404


def test_route_goes_to_damaged_sites_only(run_id):
    r = client.get(f"/api/runs/{run_id}/route?k=5").json()
    assert 1 <= len(r["stops"]) <= 5 and r["maps_url"].startswith("https://www.google.com/maps")
    assert [s["visit_order"] for s in r["stops"]] == list(range(1, len(r["stops"]) + 1))
    assert all(s["severity"] != "none" for s in r["stops"])


@pytest.mark.parametrize("fmt,needle", [("csv", "rank"), ("geojson", "FeatureCollection"), ("kml", "<kml"), ("report", "<html")])
def test_exports(run_id, fmt, needle):
    r = client.get(f"/api/runs/{run_id}/exports/{fmt}")
    assert r.status_code == 200 and needle in r.text and "attachment" in r.headers["content-disposition"]


def test_evaluation_for_demo(run_id):
    ev = client.get(f"/api/runs/{run_id}/evaluation").json()
    assert ev["available"] and 0 <= ev["accuracy"] <= 1 and len(ev["confusion"]) == 4


def test_unknown_run_and_bad_input():
    assert client.get("/api/runs/doesnotexist").status_code == 404
    assert client.post("/api/runs", data={"source": "upload"}).status_code == 400          # no files
    assert client.post("/api/runs", data={"source": "other"}).status_code == 400
    assert client.post("/api/runs", data={"minor": "7"}).status_code == 422               # out of range
    r = client.post("/api/runs", data={"source": "upload"},
                    files={"pre": ("a.exe", b"x", "application/octet-stream"), "post": ("b.png", b"x", "image/png")})
    assert r.status_code == 400
