"""OLD Streamlit interface, kept for reference only.  Run:  streamlit run legacy_streamlit_app.py"""
import io, os, tempfile
import cv2, numpy as np, pandas as pd, streamlit as st
import base64
from damage_analyzer import exporters as ex
from damage_analyzer.evaluate import report as eval_report
from damage_analyzer.geo import GeoRef, poly_to_px
from damage_analyzer.io_utils import load_image, load_footprints, load_pois, load_roads, grid_zones
from damage_analyzer.pipeline import analyze, render_overlay
from damage_analyzer.routing import plan_route
from damage_analyzer.scoring import Config

def show_html(html_str, height):
    """Embed HTML in an opaque-origin data: iframe (safe for user-supplied GeoJSON names)."""
    if hasattr(st, "iframe"):
        st.iframe("data:text/html;base64," + base64.b64encode(html_str.encode()).decode(), height=height)
    else:
        import streamlit.components.v1 as components
        components.html(html_str, height=height)


DEMO = os.path.join(os.path.dirname(__file__), "demo_data")
st.set_page_config(page_title="Satellite Damage Analyzer", page_icon="🛰️", layout="wide")
st.markdown("""<style>.block-container{padding-top:1.2rem}
.tier{display:inline-block;padding:2px 10px;border-radius:12px;font-weight:700;font-size:13px;margin-right:6px}
.p1{background:#fde3e3;color:#b01c1c}.p2{background:#ffebd1;color:#a35400}.p3{background:#fff4cc;color:#7a5d00}</style>""", unsafe_allow_html=True)
st.title("🛰️ Satellite Infrastructure Damage Analyzer")
st.caption("Compare BEFORE and AFTER satellite images → find damaged buildings & roads → get a GPS-located, priority-ranked inspection list.")

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("1 · Data")
    mode = st.radio("Source", ["Demo (synthetic town)", "Upload my own"], label_visibility="collapsed")
    up = {}
    if mode.startswith("Upload"):
        up["pre"] = st.file_uploader("BEFORE image (GeoTIFF/PNG/JPG)", type=["tif", "tiff", "png", "jpg", "jpeg"])
        up["post"] = st.file_uploader("AFTER image (same area)", type=["tif", "tiff", "png", "jpg", "jpeg"])
        up["foot"] = st.file_uploader("Building footprints (GeoJSON, optional)", type=["geojson", "json"])
        up["pois"] = st.file_uploader("Hospitals/schools (GeoJSON, optional)", type=["geojson", "json"])
        up["roads"] = st.file_uploader("Roads (GeoJSON, optional)", type=["geojson", "json"])
        st.caption("PNG/JPG have no GPS tags → enter the area's bounds:")
        c1, c2 = st.columns(2)
        west = c1.number_input("West lon", value=82.2500, format="%.5f"); east = c2.number_input("East lon", value=82.2546, format="%.5f")
        south = c1.number_input("South lat", value=16.9854, format="%.5f"); north = c2.number_input("North lat", value=16.9900, format="%.5f")
    st.header("2 · Settings")
    with st.expander("Severity thresholds (damage score 0–1)"):
        t_min = st.slider("minor ≥", 0.05, 0.6, 0.22, 0.01)
        t_maj = st.slider("major ≥", 0.1, 0.8, 0.42, 0.01)
        t_des = st.slider("destroyed ≥", 0.3, 0.95, 0.68, 0.01)
    with st.expander("Base camp for the field route"):
        base_lat = st.number_input("Base lat", value=16.9855, format="%.6f"); base_lon = st.number_input("Base lon", value=82.2500, format="%.6f")
        k_route = st.slider("Sites in route", 3, 15, 8)
    run = st.button("▶ Analyze damage", type="primary", use_container_width=True)


def _tmp(uploaded):
    suffix = os.path.splitext(uploaded.name)[1]
    f = tempfile.NamedTemporaryFile(delete=False, suffix=suffix); f.write(uploaded.getvalue()); f.close(); return f.name


def _load_inputs():
    if mode.startswith("Demo"):
        pre, geo = load_image(f"{DEMO}/pre.tif"); post, _ = load_image(f"{DEMO}/post.tif")
        gt = pd.read_csv(f"{DEMO}/ground_truth.csv")
        return pre, post, geo, load_footprints(f"{DEMO}/footprints.geojson"), load_pois(f"{DEMO}/pois.geojson"), load_roads(f"{DEMO}/roads.geojson"), gt
    if not (up["pre"] and up["post"]):
        raise ValueError("Please upload both the BEFORE and AFTER images.")
    pre, geo = load_image(_tmp(up["pre"])); post, _ = load_image(_tmp(up["post"]))
    if geo is None:
        geo = GeoRef.from_bounds(west, south, east, north, pre.shape)
    foot = load_footprints(_tmp(up["foot"])) if up["foot"] else grid_zones(geo, pre.shape)
    pois = load_pois(_tmp(up["pois"])) if up["pois"] else []
    roads = load_roads(_tmp(up["roads"])) if up["roads"] else []
    return pre, post, geo, foot, pois, roads, None


if run:
    try:
        cfg = Config(minor=t_min, major=max(t_maj, t_min), destroyed=max(t_des, t_maj))
        with st.spinner("Aligning → detecting change → scoring → ranking …"):
            pre, post, geo, foot, pois, roads, gt = _load_inputs()
            res = analyze(pre, post, geo, foot, pois, roads, cfg)
        st.session_state.update(res=res, gt=gt, base=(base_lon, base_lat), k=k_route, demo=mode.startswith("Demo"))
    except Exception as e:
        st.error(f"Could not analyze: {e}")

res = st.session_state.get("res")
if res is None:
    st.info("👈 Pick **Demo** and press **Analyze damage** to see the full workflow. Real imagery: see README → *Getting real data*.")
    st.stop()

if st.session_state.get("demo"):
    st.warning("SYNTHETIC DEMO DATA – a generated town with known damage, used to test the pipeline. Not a real event.")
for w in res.warnings: st.warning(w)
s = res.summary
m = st.columns(6)
m[0].metric("Sites analysed", s["buildings"]); m[1].metric("Destroyed", s["destroyed"]); m[2].metric("Major", s["major"])
m[3].metric("Minor", s["minor"]); m[4].metric("Road segments flagged", s["road_flags"]); m[5].metric("Critical facilities hit", s["critical_facilities_hit"])

tabs = st.tabs(["① Before / After", "② Damage map", "③ Priority list + GPS", "④ Field route", "⑤ Live map", "⑥ Why this rank?", "⑦ Accuracy check"])

with tabs[0]:
    a = st.slider("Blend (left = BEFORE, right = AFTER)", 0.0, 1.0, 0.5, 0.05)
    c1, c2 = st.columns(2)
    c1.image(res.pre, caption="BEFORE", width="stretch"); c2.image(res.post, caption="AFTER (aligned)", width="stretch")
    st.image(cv2.addWeighted(res.pre, 1 - a, res.post, a, 0), caption="Blend", width="stretch")
    ai = res.align_info
    st.caption(f"Alignment: {ai['quality']} · {ai['inliers']} matched points · shift {ai['shift_px']:.1f}px · rotation {ai['rotation_deg']:.2f}°")

with tabs[1]:
    st.markdown('<span class="tier p1">Red = destroyed</span><span class="tier p2">Orange = major</span><span class="tier p3">Yellow = minor</span> '
                'Green = no change · Purple line = possible road obstruction · numbers = inspect-first rank', unsafe_allow_html=True)
    ov = render_overlay(res)
    st.image(ov, width="stretch")
    heat = cv2.applyColorMap((res.fields.heat * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    with st.expander("Raw change heat-map (before filtering to buildings)"):
        st.image(cv2.cvtColor(heat, cv2.COLOR_BGR2RGB), width="stretch")

with tabs[2]:
    f1, f2 = st.columns([1, 3])
    tiers = f1.multiselect("Priority tiers", ["P1", "P2", "P3"], default=["P1", "P2"])
    f2.markdown('<span class="tier p1">P1 URGENT</span><span class="tier p2">P2 HIGH</span><span class="tier p3">P3 ROUTINE</span> '
                'Rank = Priority Index (damage × facility importance × neighbourhood damage).', unsafe_allow_html=True)
    lst = ex.listing(res); lst = lst[lst.tier.isin(tiers)]
    st.dataframe(lst, width="stretch", height=380, hide_index=True,
                 column_config={"maps_link": st.column_config.LinkColumn("Google Maps", display_text="Open"),
                                "lat": st.column_config.NumberColumn(format="%.6f"), "lon": st.column_config.NumberColumn(format="%.6f")})
    d1, d2, d3, d4 = st.columns(4)
    d1.download_button("⬇ CSV", ex.to_csv(res), "priority_list.csv", "text/csv", width="stretch")
    d2.download_button("⬇ GeoJSON", ex.to_geojson(res), "damage.geojson", "application/geo+json", width="stretch")
    d3.download_button("⬇ KML (Google Earth / phone)", ex.to_kml(res), "damage.kml", "application/vnd.google-earth.kml+xml", width="stretch")
    d4.download_button("⬇ Report (HTML → print to PDF)", ex.to_report_html(res, base_lonlat=st.session_state["base"]), "damage_report.html", "text/html", width="stretch")

with tabs[3]:
    route, km, url = plan_route(res.df, st.session_state["base"], st.session_state["k"])
    st.markdown(f"**Suggested order for one inspection team** – most urgent site first, then nearest-next. Total ≈ **{km} km** from base camp.")
    if url: st.link_button("🧭 Open route in Google Maps", url)
    st.dataframe(route[["visit_order", "id", "tier", "severity", "priority_index", "lat", "lon", "facility"]], hide_index=True, width="stretch")
    st.caption("Greedy ordering (urgent first, then nearest) – a fast, explainable heuristic, not a guaranteed shortest tour.")

with tabs[4]:
    show_html(ex.to_web_map(res), 600)
    st.caption("Satellite basemap (Esri) loads from the internet in your browser. Click a marker for GPS + Google Maps link.")

with tabs[5]:
    ids = list(ex.listing(res).id)
    pick = st.selectbox("Choose a site", ids)
    r = res.df[res.df.id == pick].iloc[0]
    cx, cy = res.georef.lonlat_to_px(r.lon, r.lat); cx, cy = int(cx), int(cy); R = 42
    y0, x0 = max(0, cy - R), max(0, cx - R)
    c1, c2, c3 = st.columns(3)
    c1.image(res.pre[y0:cy + R, x0:cx + R], caption="BEFORE", width="stretch")
    c2.image(res.post[y0:cy + R, x0:cx + R], caption="AFTER", width="stretch")
    hm = cv2.cvtColor(cv2.applyColorMap((res.fields.heat * 255).astype(np.uint8), cv2.COLORMAP_INFERNO), cv2.COLOR_BGR2RGB)
    c3.image(hm[y0:cy + R, x0:cx + R], caption="CHANGE HEAT", width="stretch")
    st.markdown(f"**{r.id}** · {r.severity.upper()} · tier **{r.tier}** · confidence **{r.confidence}** · GPS `{r.lat:.6f}, {r.lon:.6f}`")
    cfg = res.cfg
    st.markdown(f"""
| Evidence (inside this footprint) | Value |
|---|---|
| Share of roof area that changed a lot | {r.changed_fraction:.0%} |
| Structural dissimilarity (SSIM-based) | {r.dissimilarity:.2f} |
| Edge/texture change | {r.edge_change:.2f} |
| **Damage score** | **{r.damage_score:.2f}** |
| Facility importance | {r.criticality:.1f} {('– ' + r.facility) if r.facility else ''} |
| Neighbourhood damage share | {r.hotspot:.0%} |
| **Priority Index** = 100 × {r.damage_score:.2f} × (1+{r.criticality:.1f}) × (1+0.5×{r.hotspot:.2f}) | **{r.priority_index}** |""")
    st.caption("Confidence is a heuristic (distance to the nearest severity threshold, lowered if alignment is weak) – not a calibrated probability.")

with tabs[6]:
    gt = st.session_state.get("gt")
    if gt is None:
        up_gt = st.file_uploader("Optional: ground-truth CSV with columns id, gt (none/minor/major/destroyed)", type=["csv"])
        gt = pd.read_csv(up_gt) if up_gt else None
    if gt is None:
        st.info("Upload labelled data (e.g. converted from xBD) to measure accuracy.")
    else:
        rep, mm = eval_report(res, gt)
        if st.session_state.get("demo"): st.warning("Measured on SYNTHETIC data – proves the pipeline works, NOT real-world accuracy. Validate on xBD for a real number.")
        a, b, c, d = st.columns(4)
        a.metric("Exact severity accuracy", f"{rep['accuracy']:.0%}"); b.metric("Macro-F1", f"{rep['macro_f1']:.2f}")
        c.metric("Damaged-vs-not F1", f"{rep['damaged_vs_not']['f1']:.2f}"); d.metric("Top-10 precision", f"{rep['top10_precision']:.0%}")
        st.write("Confusion matrix (rows = truth, columns = prediction)"); st.dataframe(rep["confusion"], width="stretch")

with st.expander("How is the Priority Index computed? (what makes this different from a plain heat-map)"):
    st.markdown("""**PI = 100 × damage score × (1 + facility importance) × (1 + 0.5 × neighbourhood damage share)**
- *damage score*: how much of the building changed (share of changed roof, structure similarity, edge loss).
- *facility importance*: hospital 1.0 · fire station 0.8 · clinic 0.7 · school/shelter 0.6 … (editable in `Config.crit_weights`).
- *neighbourhood share*: share of buildings within 80 m that are severely damaged → clusters where many people may need help.
Then the top sites are ordered into a field route and exported with exact GPS.""")
