"""Damage Analyzer backend.

WHAT THIS FILE IS
  A small web server. The browser page in web/ never runs the analysis itself. It sends the
  images to this server, the server runs your existing pipeline (damage_analyzer/), and the
  page then asks for the results one piece at a time (numbers, pictures, downloads).

HOW TO RUN IT
      pip install -r requirements.txt
      python server.py
  then open http://127.0.0.1:8000   (interactive API docs: http://127.0.0.1:8000/docs)

THE FLOW  (read this top to bottom once and the whole backend makes sense)
  1. POST /api/runs               start an analysis, get back a run_id immediately
  2. GET  /api/runs/{id}          poll: "running" (with a stage message) -> "done" or "error"
  3. GET  /api/runs/{id}/sites    the ranked list as JSON
     GET  /api/runs/{id}/images/{pre|post|overlay|heat}.jpg
     GET  /api/runs/{id}/route, /exports/{csv|geojson|kml|report}, /map, /evaluation ...
Everything lives in memory. Restarting the server forgets old runs. That is fine for a demo; the
BACKEND_GUIDE.md explains what you would add (a database, a job queue) for real deployment.
"""
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np
import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from damage_analyzer import exporters as ex
from damage_analyzer.evaluate import report as eval_report
from damage_analyzer.geo import GeoRef
from damage_analyzer.io_utils import grid_zones, load_footprints, load_image, load_pois, load_roads
from damage_analyzer.pipeline import analyze, render_overlay
from damage_analyzer.routing import plan_route
from damage_analyzer.scoring import SEVERITIES, Config

log = logging.getLogger("damage-analyzer")

ROOT = Path(__file__).resolve().parent
DEMO_DIR = ROOT / "demo_data"
WEB_DIR = ROOT / "web"

MAX_RUNS_KEPT = 8                     # oldest runs are forgotten beyond this (keeps memory bounded)
MAX_UPLOAD_MB = 300                   # per file
MAX_CSV_MB = 5
IMAGE_EXT = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}
VECTOR_EXT = {".geojson", ".json"}
TIER_NAME = ex.TIER_NAME

# Only this many analyses run at once; the rest wait in line. (Image processing is heavy.)
_slots = threading.Semaphore(2)


# ============================================================================ run bookkeeping
@dataclass
class Run:
    """Everything the server remembers about one analysis."""
    id: str
    status: str = "running"                    # running | done | error
    stage: str = "Starting…"
    error: Optional[str] = None
    demo: bool = False
    base: tuple = (82.25, 16.9855)             # (lon, lat) of the base camp
    route_size: int = 8
    result: Any = None                         # damage_analyzer.pipeline.Result
    truth: Optional[pd.DataFrame] = None       # optional ground-truth labels (id, gt)
    cache: dict = field(default_factory=dict)  # rendered images / html, so we draw each once
    lock: threading.Lock = field(default_factory=threading.Lock)


RUNS: "OrderedDict[str, Run]" = OrderedDict()
RUNS_LOCK = threading.Lock()


def new_run(**kw) -> Run:
    run = Run(id=uuid.uuid4().hex, **kw)
    with RUNS_LOCK:
        RUNS[run.id] = run
        while len(RUNS) > MAX_RUNS_KEPT:
            RUNS.popitem(last=False)
    return run


def get_run(run_id: str, need_done: bool = True) -> Run:
    with RUNS_LOCK:
        run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, "Unknown run. The server may have restarted. Analyze again.")
    if need_done:
        if run.status == "running":
            raise HTTPException(409, "This analysis is still running.")
        if run.status == "error":
            raise HTTPException(409, run.error or "This analysis failed.")
    return run


def _plain(obj):
    """json.dumps helper: numpy numbers -> Python numbers."""
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError(f"not JSON serialisable: {type(obj)}")


def clean(obj):
    """Round-trip through JSON so numpy types and NaN-free values are guaranteed."""
    return json.loads(json.dumps(obj, default=_plain))


# ============================================================================ reading uploads
def save_upload(up: UploadFile, folder: Path, stem: str, allowed: set, limit_mb: int) -> Path:
    """Copy one uploaded file to disk (never trusting its name) and enforce type + size."""
    suffix = Path(up.filename or "").suffix.lower()
    if suffix not in allowed:
        raise HTTPException(400, f"“{up.filename}” is not an accepted file type. Use: {', '.join(sorted(allowed))}.")
    dest = folder / f"{stem}{suffix}"
    limit = limit_mb * 1024 * 1024
    size = 0
    with open(dest, "wb") as out:
        while True:
            chunk = up.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, f"“{up.filename}” is larger than {limit_mb} MB.")
            out.write(chunk)
    if size == 0:
        raise HTTPException(400, f"“{up.filename}” is empty.")
    return dest


def demo_inputs():
    pre, geo = load_image(DEMO_DIR / "pre.tif")
    post, _ = load_image(DEMO_DIR / "post.tif")
    gt = pd.read_csv(DEMO_DIR / "ground_truth.csv")
    return (pre, post, geo, load_footprints(DEMO_DIR / "footprints.geojson"),
            load_pois(DEMO_DIR / "pois.geojson"), load_roads(DEMO_DIR / "roads.geojson"), gt)


def uploaded_inputs(paths: dict, bounds: tuple):
    pre, geo = load_image(paths["pre"])
    post, _ = load_image(paths["post"])
    if geo is None:                                    # PNG/JPG carry no GPS: use the bounds typed by the user
        west, south, east, north = bounds
        geo = GeoRef.from_bounds(west, south, east, north, pre.shape)
    foot = load_footprints(paths["foot"]) if "foot" in paths else grid_zones(geo, pre.shape)
    pois = load_pois(paths["pois"]) if "pois" in paths else []
    roads = load_roads(paths["roads"]) if "roads" in paths else []
    return pre, post, geo, foot, pois, roads, None


# ============================================================================ the background job
def run_job(run: Run, load_inputs: Callable, cfg: Config, workdir: Optional[Path]):
    """Runs in its own thread so the POST request can return straight away."""
    try:
        run.stage = "Waiting for another analysis to finish…"
        with _slots:
            run.stage = "Reading the images…"
            pre, post, geo, foot, pois, roads, gt = load_inputs()
            res = analyze(pre, post, geo, foot, pois, roads, cfg, progress=lambda msg: setattr(run, "stage", msg))
        if res.df.empty:
            raise ValueError("No buildings could be scored. Check that the footprints overlap the image area.")
        run.result, run.truth = res, gt
        run.stage, run.status = "Done", "done"
    except ValueError as e:                            # problems we expect and can explain
        run.error, run.status = str(e), "error"
    except Exception as e:                             # anything else: log the details, show something short
        log.exception("analysis failed")
        run.error, run.status = f"Could not analyze these files ({type(e).__name__}: {e}).", "error"
    finally:
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)  # uploads are already in memory; delete the temp files


# ============================================================================ the app
app = FastAPI(title="Damage Analyzer API", version="1.0")


@app.middleware("http")
async def basic_security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


@app.get("/api/health")
def health():
    return {"ok": True, "runs_in_memory": len(RUNS)}


# ---------------------------------------------------------------------------- 1. start
@app.post("/api/runs", status_code=202)
def start_run(
    source: str = Form("demo"),
    minor: float = Form(0.22, ge=0.0, le=1.0),
    major: float = Form(0.42, ge=0.0, le=1.0),
    destroyed: float = Form(0.68, ge=0.0, le=1.0),
    base_lat: float = Form(16.9855, ge=-90.0, le=90.0),
    base_lon: float = Form(82.25, ge=-180.0, le=180.0),
    route_size: int = Form(8, ge=1, le=50),
    west: float = Form(82.25, ge=-180.0, le=180.0),
    east: float = Form(82.2546, ge=-180.0, le=180.0),
    south: float = Form(16.9854, ge=-90.0, le=90.0),
    north: float = Form(16.99, ge=-90.0, le=90.0),
    pre: Optional[UploadFile] = File(None),
    post: Optional[UploadFile] = File(None),
    foot: Optional[UploadFile] = File(None),
    pois: Optional[UploadFile] = File(None),
    roads: Optional[UploadFile] = File(None),
):
    if source not in ("demo", "upload"):
        raise HTTPException(400, "source must be “demo” or “upload”.")
    t_minor = minor
    t_major = max(major, t_minor)                      # keep the thresholds in order
    t_destroyed = max(destroyed, t_major)
    cfg = Config(minor=t_minor, major=t_major, destroyed=t_destroyed)

    if source == "demo":
        run = new_run(demo=True, base=(base_lon, base_lat), route_size=route_size)
        threading.Thread(target=run_job, args=(run, demo_inputs, cfg, None), daemon=True).start()
        return {"run_id": run.id, "status": run.status}

    # ---- uploaded files: validate everything BEFORE starting the job, so mistakes come back as clear errors
    if pre is None or post is None:
        raise HTTPException(400, "Upload both a before image and an after image.")
    if not (west < east and south < north):
        raise HTTPException(400, "Bounds must satisfy west < east and south < north.")
    workdir = Path(tempfile.mkdtemp(prefix="damage_"))
    try:
        paths = {"pre": save_upload(pre, workdir, "pre", IMAGE_EXT, MAX_UPLOAD_MB),
                 "post": save_upload(post, workdir, "post", IMAGE_EXT, MAX_UPLOAD_MB)}
        for key, up in (("foot", foot), ("pois", pois), ("roads", roads)):
            if up is not None and up.filename:
                paths[key] = save_upload(up, workdir, key, VECTOR_EXT, MAX_UPLOAD_MB)
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    run = new_run(demo=False, base=(base_lon, base_lat), route_size=route_size)
    loader = lambda: uploaded_inputs(paths, (west, south, east, north))  # noqa: E731
    threading.Thread(target=run_job, args=(run, loader, cfg, workdir), daemon=True).start()
    return {"run_id": run.id, "status": run.status}


# ---------------------------------------------------------------------------- 2. poll
@app.get("/api/runs/{run_id}")
def run_status(run_id: str):
    run = get_run(run_id, need_done=False)
    out = {"run_id": run.id, "status": run.status, "stage": run.stage, "error": run.error}
    if run.status == "done":
        res = run.result
        h, w = res.pre.shape[:2]
        out.update(
            demo=run.demo, summary=res.summary, warnings=res.warnings, alignment=res.align_info,
            image={"w": int(w), "h": int(h)}, route_size=run.route_size,
            base={"lat": run.base[1], "lon": run.base[0]}, has_truth=run.truth is not None)
    return JSONResponse(clean(out))


# ---------------------------------------------------------------------------- 3. the results
SITE_COLS = ["rank", "id", "type", "tier", "severity", "priority_index", "damage_score", "confidence", "lat", "lon",
             "facility", "hotspot", "area_m2", "criticality", "changed_fraction", "dissimilarity", "edge_change", "maps_link"]


def _px(res, lons, lats):
    cx, cy = res.georef.lonlat_to_px(np.asarray(lons, float), np.asarray(lats, float))
    return [{"x": float(a), "y": float(b)} for a, b in zip(np.atleast_1d(cx), np.atleast_1d(cy))]


@app.get("/api/runs/{run_id}/sites")
def sites(run_id: str, include_none: bool = False):
    res = get_run(run_id).result
    df = res.df if include_none else res.df[res.df.severity != "none"]
    df = df[SITE_COLS].reset_index(drop=True)
    rows = json.loads(df.to_json(orient="records"))   # pandas turns NaN into null for us
    for row, p in zip(rows, _px(res, df.lon, df.lat)):
        row["tier_name"] = TIER_NAME.get(row["tier"], row["tier"])
        row["px"] = p
    return {"sites": rows}


@app.get("/api/runs/{run_id}/route")
def route(run_id: str, k: int = 8):
    run = get_run(run_id)
    res = run.result
    k = max(1, min(50, k))
    damaged = res.df[res.df.severity != "none"]        # never send a team to an undamaged site
    ordered, km, url = plan_route(damaged, run.base, k)
    stops = []
    if not ordered.empty:
        for r, p in zip(ordered.to_dict("records"), _px(res, ordered.lon, ordered.lat)):
            stops.append({"visit_order": int(r["visit_order"]), "id": r["id"], "tier": r["tier"],
                          "tier_name": TIER_NAME.get(r["tier"], r["tier"]), "severity": r["severity"],
                          "priority_index": float(r["priority_index"]), "lat": float(r["lat"]), "lon": float(r["lon"]),
                          "facility": r["facility"] or "", "px": p})
    h, w = res.pre.shape[:2]
    bx, by = res.georef.lonlat_to_px(run.base[0], run.base[1])
    base_px = {"x": float(bx), "y": float(by)} if (0 <= bx <= w and 0 <= by <= h) else None
    return clean({"km": km, "maps_url": url, "stops": stops, "base_px": base_px})


# ---------------------------------------------------------------------------- pictures
def jpeg(rgb: np.ndarray, quality: int = 88) -> bytes:
    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise HTTPException(500, "Could not encode the image.")
    return buf.tobytes()


def heat_rgb(run: Run) -> np.ndarray:
    with run.lock:
        if "heat_rgb" not in run.cache:
            bgr = cv2.applyColorMap((run.result.fields.heat * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
            run.cache["heat_rgb"] = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        return run.cache["heat_rgb"]


def image_response(data: bytes) -> Response:
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/runs/{run_id}/images/{kind}.jpg")
def image(run_id: str, kind: str):
    run = get_run(run_id)
    res = run.result
    if kind not in ("pre", "post", "overlay", "heat"):
        raise HTTPException(404, "Unknown image. Use pre, post, overlay or heat.")
    with run.lock:
        data = run.cache.get("img:" + kind)
    if data is None:
        arr = {"pre": lambda: res.pre, "post": lambda: res.post,
               "overlay": lambda: render_overlay(res), "heat": lambda: heat_rgb(run)}[kind]()
        data = jpeg(arr)
        with run.lock:
            run.cache["img:" + kind] = data
    return image_response(data)


@app.get("/api/runs/{run_id}/sites/{rank}/crop/{kind}.jpg")
def crop(run_id: str, rank: int, kind: str, radius: int = 42):
    """A square close-up around one site, from the before image, the after image or the change heat-map."""
    run = get_run(run_id)
    res = run.result
    if kind not in ("pre", "post", "heat"):
        raise HTTPException(404, "Unknown crop. Use pre, post or heat.")
    row = res.df[res.df["rank"] == rank]
    if row.empty:
        raise HTTPException(404, "No site with that rank.")
    r = row.iloc[0]
    R = max(16, min(200, radius))
    cx, cy = res.georef.lonlat_to_px(r.lon, r.lat)
    cx, cy = int(round(float(cx))), int(round(float(cy)))
    src = {"pre": res.pre, "post": res.post}.get(kind)
    if src is None:
        src = heat_rgb(run)
    h, w = src.shape[:2]
    if not (0 <= cx < w and 0 <= cy < h):
        raise HTTPException(404, "That site is outside the image.")
    padded = np.pad(src, ((R, R), (R, R), (0, 0)), mode="constant")      # black margin when the site is near an edge
    tile = padded[cy:cy + 2 * R, cx:cx + 2 * R]                           # padded coords = original + R, so this is centred
    tile = cv2.resize(tile, (420, 420), interpolation=cv2.INTER_CUBIC)
    return image_response(jpeg(tile, 90))


# ---------------------------------------------------------------------------- downloads and map
def attachment(content: str, filename: str, media_type: str) -> Response:
    return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/runs/{run_id}/exports/{fmt}")
def export(run_id: str, fmt: str):
    run = get_run(run_id)
    res = run.result
    if fmt == "csv":
        return attachment(ex.to_csv(res), "priority_list.csv", "text/csv")
    if fmt == "geojson":
        return attachment(ex.to_geojson(res), "damage.geojson", "application/geo+json")
    if fmt == "kml":
        return attachment(ex.to_kml(res), "damage.kml", "application/vnd.google-earth.kml+xml")
    if fmt == "report":
        return attachment(ex.to_report_html(res, base_lonlat=run.base), "damage_report.html", "text/html")
    raise HTTPException(404, "Unknown format. Use csv, geojson, kml or report.")


@app.get("/api/runs/{run_id}/map", response_class=HTMLResponse)
def web_map(run_id: str):
    run = get_run(run_id)
    with run.lock:
        html_doc = run.cache.get("map")
    if html_doc is None:
        html_doc = ex.to_web_map(run.result)
        with run.lock:
            run.cache["map"] = html_doc
    # The map page runs scripts, so lock it into an opaque origin as an extra layer of safety.
    return HTMLResponse(html_doc, headers={"Content-Security-Policy": "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox"})


# ---------------------------------------------------------------------------- accuracy
def evaluation_payload(run: Run) -> dict:
    if run.truth is None:
        return {"available": False}
    rep, _ = eval_report(run.result, run.truth)
    if rep["n"] == 0:
        raise HTTPException(400, "None of the ids in the CSV match the buildings that were analyzed.")
    return clean({
        "available": True, "n": rep["n"], "accuracy": rep["accuracy"], "macro_f1": rep["macro_f1"],
        "damaged_f1": rep["damaged_vs_not"]["f1"], "top10_precision": rep["top10_precision"],
        "labels": SEVERITIES, "confusion": rep["confusion"].astype(int).values.tolist()})


@app.get("/api/runs/{run_id}/evaluation")
def evaluation(run_id: str):
    return evaluation_payload(get_run(run_id))


@app.post("/api/runs/{run_id}/truth")
def upload_truth(run_id: str, truth: UploadFile = File(...)):
    run = get_run(run_id)
    folder = Path(tempfile.mkdtemp(prefix="truth_"))
    try:
        path = save_upload(truth, folder, "truth", {".csv"}, MAX_CSV_MB)
        try:
            df = pd.read_csv(path)
        except Exception:
            raise HTTPException(400, "That file is not a readable CSV.")
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    df.columns = [str(c).strip().lower() for c in df.columns]
    if not {"id", "gt"} <= set(df.columns):
        raise HTTPException(400, "The CSV needs the columns id and gt.")
    df = df[["id", "gt"]].copy()
    df["id"] = df["id"].astype(str)
    df["gt"] = df["gt"].astype(str).str.strip().str.lower()
    bad = sorted(set(df["gt"]) - set(SEVERITIES))
    if bad:
        raise HTTPException(400, f"Unknown labels in gt: {', '.join(bad[:5])}. Use none, minor, major or destroyed.")
    previous, run.truth = run.truth, df
    try:
        return evaluation_payload(run)                   # validates that ids match, then replies with the numbers
    except HTTPException:
        run.truth = previous                             # keep the old labels if the new file is unusable
        raise


# ============================================================================ the web page itself
# Mounted last so that /api/... routes win. html=True serves index.html at "/".
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


def main():
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    host = os.environ.get("HOST", "127.0.0.1")           # 127.0.0.1 = only this computer can connect
    port = int(os.environ.get("PORT", "8000"))
    print(f"Damage Analyzer is running at http://{host}:{port}   (Ctrl+C to stop)")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
