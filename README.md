HEAD
# ResQVision
# Satellite Infrastructure Damage Analyzer (PS-31)

Compare a BEFORE and an AFTER satellite image → find damaged buildings and blocked roads → give disaster teams a
**ranked, GPS-located inspection list**, a **field route**, and a downloadable report.

> **Honest status:** everything here was built and tested on a *synthetic* town (known damage). The pipeline,
> GPS output, ranking, exports and app all work. Real-data validation (xBD / Vantor) is **your next step** – steps are below.

---------------------------------------------------------------------------------------------------

## 0. Run it (10 minutes)

```bash
pip install -r requirements.txt
python -m damage_analyzer.demo_data demo_data     # (re)creates the synthetic town (already included)
python -m pytest -q tests                         # 12 tests should pass
python -m damage_analyzer.evaluate demo_data      # accuracy report + threshold calibration
streamlit run legacy_streamlit_app.py            # the OLD Streamlit interface (not the new look)
python server.py                                  # the NEW interface → open http://127.0.0.1:8000
```
The new interface (`web/`) and its backend (`server.py`) are explained in **BACKEND_GUIDE.md**.

## 1. Learn the 8 ideas (in the order the code uses them)

| # | Idea | In plain words | Where in code |
|---|------|----------------|---------------|
| 1 | **Pixel & GSD** | An image is a grid of pixels; GSD = metres per pixel (0.5 m = each pixel is 50 cm). Sentinel-2 is 10 m → a house is 1 pixel (useless for houses). | `geo.metres_per_px` |
| 2 | **Georeferencing** | A mapping *pixel (col,row) → (lon,lat)*. This is what gives **exact GPS**. GeoTIFFs carry it inside the file. | `geo.GeoRef` |
| 3 | **Footprints** | Pre-disaster building outlines (polygons) from OpenStreetMap / xBD. We judge change *inside* each outline instead of "any pixel changed". | `io_utils.load_footprints` |
| 4 | **Alignment (registration)** | Two passes never line up perfectly. ORB finds matching points; RANSAC solves shift+rotation. | `align.py` |
| 5 | **Photometric matching** | Sun angle/season changes colours. Histogram matching removes the global lighting difference first. | `scoring.prepare_fields` |
| 6 | **Change features** | (a) share of roof pixels that changed a lot (Lab colour distance), (b) SSIM structural dissimilarity, (c) edge/texture change. | `scoring.region_stats` |
| 7 | **Severity thresholds** | score → none / minor / major / destroyed. Thresholds can be **calibrated** on labelled data. | `evaluate.calibrate_thresholds` |
| 8 | **Priority Index (your uniqueness)** | `PI = 100 × damage × (1 + facility importance) × (1 + 0.5 × neighbourhood damage share)` | `scoring.priority_index` |

Read the files in this order: `geo.py → align.py → scoring.py → pipeline.py → exporters.py → app.py`. Each has a docstring explaining *why*.

## 2. What makes it different (say this to evaluators)

1. **Exact GPS for every flagged site** (CSV / GeoJSON / **KML** for Google Earth & phones, Google Maps link per row).
2. **Priority Index**, not just a heat-map: a damaged hospital outranks a damaged house; clusters of destroyed buildings rank higher.
   Weights are an explicit, editable *policy* (`Config.crit_weights`), shown on the evidence card.
3. **Field route**: top-N sites ordered (most urgent first, then nearest-next) with a Google Maps multi-stop link.
4. **Explainable**: the "Why this rank?" tab shows before/after crops, change heat, and every number in the formula.
5. **Trust gate**: alignment quality + cloud warnings; confidence labels (heuristic, *not* calibrated probabilities).
6. **Measurable**: built-in accuracy tab (confusion matrix, top-10 precision) – plug in labels and you get real numbers.

## 3. Getting real data (answers "where do officers get images, where do I?")

**How agencies really get it** (not a single portal – several channels):
- **ISRO / NRSC** is India's member of the **International Charter "Space and Major Disasters"**; authorised users (e.g. civil-protection
  bodies) can request activation and member agencies provide imagery free for relief.
- **Copernicus Emergency Management Service (Rapid Mapping)** – authorised users activate it; maps/data are generally public (except sensitive
  activations) and delivered in hours–days.
- **Commercial / open-data**: the **Vantor Open Data Program** (formerly Maxar Open Data) releases pre- and post-event 30–80 cm imagery for major
  disasters.
- Typically: *pre-event = archive image, post-event = emergency collection* → exactly the "two GPS-tagged images" our app takes.

**What YOU should use (all free, legal, hackathon-friendly):**

| Need | Source | Notes |
|---|---|---|
| Real pre/post pairs with labels | **xBD (xView2)** – register at xview2.org | 22k images, 850k labelled buildings, 4 damage levels, includes Nepal-India-Bangladesh floods 2017. Read the licence on the download page (I could not load it to confirm terms). Images are PNG without geotags; the label JSON has lon/lat polygons – `load_xbd_pair` fits the georeferencing from them. |
| Real recent event imagery | **Vantor Open Data** – `s3://vantor-opendata/` (no account), STAC catalog, QGIS plugin, web app `opengeos.org/maplibre-gl-vantor` | Cloud-Optimized GeoTIFFs, CC BY-NC 4.0 (non-commercial, cite it). Files are GBs → clip with `clip_pair.py`. |
| Building outlines + hospitals/schools/roads | **OpenStreetMap** via `fetch_osm.py` | Run on your laptop (needs internet). |
| Wide-area flood/burn extent only | Sentinel-1/2 | 10 m – too coarse for houses. Mention, don't use for building damage. |

**Workflow A – quickest believable real test (xBD):**
1. Download one disaster's tile pair + JSON. 2. `pre, post, geo, foot, gt = load_xbd_pair(...)`  (in `io_utils.py`).
3. `res = analyze(pre, post, geo, foot)`; 4. compare `res.df` severity with `gt`; 5. `calibrate_thresholds` on 20–50 tiles; paste the best thresholds
into `Config` / the sidebar sliders. **Report the real accuracy you measure** – even 60–70% with an honest confusion matrix beats an unverifiable claim.

**Workflow B – real event (Vantor):** pick an event in the STAC browser → download one pre and one post COG →
`python clip_pair.py --pre pre.tif --post post.tif --bbox W S E N --gsd 0.5 --out real_pair` → `python fetch_osm.py --bbox W S E N --out osm` →
in the app choose *Upload my own* and drop `pre.tif`, `post.tif`, `footprints/pois/roads.geojson`.
(No footprints? The app falls back to **zone mode**: 24 m grid cells scored the same way.)

## 4. Upgrade path (only after the core works)
- Replace the hand-built score with a **U-Net / xBD-trained model** (xView2 baseline: github.com/DIUx-xView/xview2-baseline). Keep our
  footprints, GPS, Priority Index, route and exports – only `region_stats`/score changes.
- Train a small classifier on the 3 features (changed_fraction, dissimilarity, edge_change) using xBD labels (scikit-learn, 30 minutes) – a
  genuine ML step that is fast and explainable.

## 5. Suggested remaining timeline (adapt to your clock)
| Block | Do |
|---|---|
| 0–1 h | Run section 0; read `geo.py`, `scoring.py`; explain each step to a teammate (teaching = learning). |
| 1–4 h | Get ONE real xBD tile or Vantor clip running through the app. Fix whatever breaks. |
| 4–7 h | Calibrate thresholds on ≥20 labelled tiles; record the real confusion matrix. |
| 7–10 h | OSM footprints/facilities for your demo area; screenshot every tab. |
| 10–13 h | (Optional) classifier upgrade or U-Net. If it isn't working at hour 12, STOP and ship the current version. |
| 13–16 h | Rehearse the 60-second pitch + 7 Q&A answers from page 2 of the sheet. Prepare a backup video of the demo. |
| 16–20 h | Sleep / buffer. Never change code in the last 2 hours. |

## 6. Known limitations (say them before the evaluator does)
- Satellite imagery shows **visible** damage only; it cannot certify structural safety. Every flagged site needs on-site verification.
- Default thresholds were set on **synthetic** data. Metrics in the app for the demo are *pipeline checks*, not real-world accuracy.
- Roads are flagged as "possible obstruction" via unusual change on a ~7 m strip – a screening hint, not validated road-damage detection.
- Confidence labels are heuristics. Priority weights are policy choices, not measured truths.
- Not tested in the build sandbox (no internet access there): `fetch_osm.py` live calls, the real xBD files (parser is tested on a fabricated
  file following the published JSON structure – verify on a real file first), the Esri satellite basemap in the Live-map tab.
- Demo/Vantor data licences are non-commercial; keep attribution.

## 7. Layout
```
server.py                 web backend (FastAPI)
web/                      new interface (index.html, styles.css, app.js)
legacy_streamlit_app.py   old Streamlit app (7 tabs)
app.py                    shortcut: python app.py starts server.py
clip_pair.py              clip/co-register two big GeoTIFFs to one grid
fetch_osm.py              OSM footprints / facilities / roads
damage_analyzer/
  geo.py  align.py  scoring.py  pipeline.py  routing.py  exporters.py  io_utils.py  evaluate.py  demo_data.py
demo_data/                synthetic pair + footprints/pois/roads + ground_truth.csv
tests/test_core.py        12 tests
sheet/                    the A4 SRS sheet (PNG + PDF with ruler guide and pitch script)
```
858130e (Initial commit : Initial mind blowing interface)
