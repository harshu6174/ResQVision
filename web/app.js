/* Damage Analyzer front end. Plain JavaScript, no build step.
   It only talks to the backend through /api/... (see server.py).
   Every piece of data from the server is inserted with textContent, never innerHTML, so file names inside a user's GeoJSON cannot inject markup. */
(() => {
  "use strict";

  /* ------------------------------------------------------------------ helpers */
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);

  /** Tiny element builder: h("div", {class: "x", onclick: fn}, "text", childNode) */
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === false || v == null) continue;
      if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return el;
  }

  const store = {
    get(k) { try { return sessionStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { sessionStorage.setItem(k, v); } catch { /* storage can be blocked; the page works without it */ } },
  };

  const api = {
    async json(url, opts) {
      let res;
      try { res = await fetch(url, opts); }
      catch { throw new Error("Cannot reach the server. Start it with: python server.py"); }
      if (!res.ok) {
        let msg = `The server answered ${res.status}.`;
        try { const j = await res.json(); if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* keep default */ }
        throw new Error(msg);
      }
      return res.json();
    },
  };

  const isMapsUrl = (u) => typeof u === "string" && /^https:\/\/www\.google\.com\/maps[/?]/.test(u);

  /* ------------------------------------------------------------------ buttons: liquid fill + bubbles */
  function arm(btn) {
    if (btn._armed) return;
    btn._armed = true;
    btn.prepend(h("span", { class: "fill", "aria-hidden": "true" }));
  }
  function pointerPos(btn, e) {
    const r = btn.getBoundingClientRect();
    return { x: clamp(e.clientX - r.left, 0, r.width), y: clamp(e.clientY - r.top, 0, r.height), r };
  }
  function spawnBubbles(btn, x, r) {
    if (reduceMotion || btn.querySelectorAll(".bubble").length > 14) return;
    for (let i = 0; i < 7; i++) {
      const size = 5 + Math.random() * 12;
      const b = h("span", { class: "bubble", "aria-hidden": "true" });
      b.style.cssText =
        `left:${clamp(x + (Math.random() - .5) * r.width * .55, 8, r.width - 8)}px;width:${size}px;height:${size}px;` +
        `--dx:${(Math.random() - .5) * 26}px;--rise:${r.height * (.7 + Math.random() * .9) + 14}px;` +
        `--t:${(.7 + Math.random() * .8).toFixed(2)}s;animation-delay:${(Math.random() * .3).toFixed(2)}s`;
      b.addEventListener("animationend", () => b.remove());
      btn.append(b);
    }
  }
  document.addEventListener("pointerover", (e) => {
    const btn = e.target.closest?.(".btn");
    if (!btn || btn.contains(e.relatedTarget) || e.pointerType === "touch") return;
    arm(btn);
    const { x, y, r } = pointerPos(btn, e);
    btn.style.setProperty("--x", x + "px");
    btn.style.setProperty("--y", y + "px");
    btn.style.setProperty("--d", Math.ceil(Math.hypot(r.width, r.height) * 2.1) + "px");
    btn.classList.add("hot");
    spawnBubbles(btn, x, r);
  });
  document.addEventListener("pointerout", (e) => {
    const btn = e.target.closest?.(".btn");
    if (!btn || btn.contains(e.relatedTarget)) return;
    const { x, y } = pointerPos(btn, e);
    btn.style.setProperty("--x", x + "px");
    btn.style.setProperty("--y", y + "px");
    btn.classList.remove("hot");
  });
  document.addEventListener("focusin", (e) => { const b = e.target.closest?.(".btn"); if (b) arm(b); });
  // A tap on a touch screen: pulse the fill once.
  document.addEventListener("pointerdown", (e) => {
    const btn = e.target.closest?.(".btn");
    if (!btn || e.pointerType !== "touch") return;
    arm(btn);
    const { x, y, r } = pointerPos(btn, e);
    btn.style.setProperty("--x", x + "px"); btn.style.setProperty("--y", y + "px");
    btn.style.setProperty("--d", Math.ceil(Math.hypot(r.width, r.height) * 2.1) + "px");
    btn.classList.add("hot"); spawnBubbles(btn, x, r);
    setTimeout(() => btn.classList.remove("hot"), 500);
  });

  /* ------------------------------------------------------------------ before/after wipe */
  function setWipe(root, p) {
    p = clamp(p, 0, 100);
    root.style.setProperty("--p", p);
    const hd = $(".handle", root);
    if (hd) hd.setAttribute("aria-valuenow", String(Math.round(p)));
    root._p = p;
  }
  function animateWipe(root, from, to, ms) {
    if (reduceMotion) { setWipe(root, to); return Promise.resolve(); }
    return new Promise((resolve) => {
      const t0 = performance.now();
      const ease = (t) => (t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
      const tick = (now) => {
        const t = clamp((now - t0) / ms, 0, 1);
        setWipe(root, from + (to - from) * ease(t));
        t < 1 ? requestAnimationFrame(tick) : resolve();
      };
      requestAnimationFrame(tick);
    });
  }
  function buildWipe(root, before, after, ratio) {
    root.replaceChildren(
      h("img", { src: before, alt: "Before the event", draggable: "false" }),
      h("img", { class: "after", src: after, alt: "After the event", draggable: "false" }),
      h("span", { class: "tag l", text: "Before" }),
      h("span", { class: "tag r", text: "After" }),
      h("div", { class: "handle", role: "slider", tabindex: "0", "aria-label": "Compare before and after", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": "50" }),
    );
    if (ratio) root.style.setProperty("--ar", String(ratio));
    setWipe(root, 50);
    if (root._wired) return;
    root._wired = true;
    const move = (e) => { const r = root.getBoundingClientRect(); setWipe(root, ((e.clientX - r.left) / r.width) * 100); };
    root.addEventListener("pointerdown", (e) => { root._drag = true; root._skipIntro = true; root.setPointerCapture(e.pointerId); move(e); });
    root.addEventListener("pointermove", (e) => { if (root._drag) move(e); });
    const stop = () => { root._drag = false; };
    root.addEventListener("pointerup", stop); root.addEventListener("pointercancel", stop);
    root.addEventListener("keydown", (e) => {
      const step = e.shiftKey ? 15 : 5;
      if (e.key === "ArrowLeft") { setWipe(root, root._p - step); e.preventDefault(); }
      if (e.key === "ArrowRight") { setWipe(root, root._p + step); e.preventDefault(); }
      if (e.key === "Home") { setWipe(root, 0); e.preventDefault(); }
      if (e.key === "End") { setWipe(root, 100); e.preventDefault(); }
    });
  }

  /* ------------------------------------------------------------------ app state */
  const state = {
    source: "demo",
    files: {},
    run: null,
    sites: [],
    view: "map",
    tiers: new Set(["P1", "P2"]),
    heat: false,
    selected: null,
    k: 8,
    rendered: {},      // which views have been drawn for the current run
  };
  const heroWipe = $("#heroWipe");
  const runUrl = (suffix) => `/api/runs/${state.run.run_id}${suffix}`;

  /* ------------------------------------------------------------------ opening animation */
  function openingSequence() {
    buildWipe(heroWipe, heroWipe.dataset.before, heroWipe.dataset.after, 1);
    const body = document.body;
    const finish = () => body.classList.remove("is-intro");
    if (reduceMotion || store.get("da-intro")) { finish(); return; }
    store.set("da-intro", "1");
    setWipe(heroWipe, 100);
    const curtain = $("#curtain");
    curtain.addEventListener("animationend", (e) => { if (e.animationName === "iris") finish(); });
    setTimeout(() => curtain.classList.add("open"), 1000);
    setTimeout(() => { if (!heroWipe._skipIntro) animateWipe(heroWipe, 100, 50, 1900); }, 1450);
    setTimeout(finish, 3600);           // safety net
  }

  /* ------------------------------------------------------------------ settings + upload dialog */
  const settingsBtn = $("#settingsBtn"), settingsPanel = $("#settings");
  function toggleSettings(open) {
    const show = open ?? settingsPanel.hidden;
    settingsPanel.hidden = !show;
    settingsBtn.setAttribute("aria-expanded", String(show));
  }
  settingsBtn.addEventListener("click", (e) => { e.stopPropagation(); toggleSettings(); });
  document.addEventListener("click", (e) => { if (!settingsPanel.hidden && !settingsPanel.contains(e.target)) toggleSettings(false); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !settingsPanel.hidden) { toggleSettings(false); settingsBtn.focus(); } });
  $$("#settings input[type=range]").forEach((r) => {
    const out = r.parentElement.querySelector("output");
    const sync = () => { out.textContent = Number(r.value).toFixed(2); };
    r.addEventListener("input", sync); sync();
  });

  const dlg = $("#uploadDlg");
  const fileInputs = { pre: $("#fPre"), post: $("#fPost"), foot: $("#fFoot"), pois: $("#fPois"), roads: $("#fRoads") };
  Object.values(fileInputs).forEach((inp) => inp.addEventListener("change", () => {
    const tile = inp.closest(".file");
    const f = inp.files[0];
    tile.classList.toggle("has", !!f);
    $(".file-hint", tile).textContent = f ? f.name : tile.dataset.hint;
    $(".file-pick", tile).textContent = f ? "Change" : "Choose";
  }));
  $$(".file").forEach((t) => { t.dataset.hint = $(".file-hint", t).textContent; });
  dlg.addEventListener("close", () => {
    if (dlg.returnValue !== "ok") return;
    state.files = {};
    for (const [k, inp] of Object.entries(fileInputs)) if (inp.files[0]) state.files[k] = inp.files[0];
    updateSourceUi();
    if (!state.files.pre || !state.files.post) showError("Choose both a before and an after image.");
    else showError("");
  });

  function updateSourceUi() {
    $$(".source-opt").forEach((b) => {
      const on = b.dataset.source === state.source;
      b.classList.toggle("is-on", on); b.setAttribute("aria-checked", String(on));
    });
    const upload = state.source === "upload";
    $("#editFilesBtn").hidden = !upload;
    $("#runHint").textContent = !upload
      ? "The demo is a generated town with known damage. It shows the whole workflow in a few seconds."
      : state.files.pre && state.files.post
        ? `Ready: ${state.files.pre.name} and ${state.files.post.name}.`
        : "Choose a before and an after image of the same area. Footprints, facilities and roads are optional.";
  }
  $$(".source-opt").forEach((b) => b.addEventListener("click", () => {
    state.source = b.dataset.source; showError(""); updateSourceUi();
    if (state.source === "upload" && !(state.files.pre && state.files.post)) dlg.showModal();
  }));
  $("#editFilesBtn").addEventListener("click", () => dlg.showModal());

  function showError(msg) {
    const el = $("#runError");
    el.textContent = msg; el.hidden = !msg;
  }

  /* ------------------------------------------------------------------ running an analysis */
  const runBtn = $("#runBtn"), runLabel = $("#runLabel");
  runBtn.addEventListener("click", () => {
    if (state.source === "upload" && !(state.files.pre && state.files.post)) { dlg.showModal(); return; }
    startRun();
  });

  function formData() {
    const fd = new FormData();
    fd.set("source", state.source);
    fd.set("minor", $("#tMin").value); fd.set("major", $("#tMaj").value); fd.set("destroyed", $("#tDes").value);
    fd.set("base_lat", $("#baseLat").value); fd.set("base_lon", $("#baseLon").value);
    fd.set("route_size", String(state.k));
    fd.set("west", $("#bW").value); fd.set("east", $("#bE").value); fd.set("south", $("#bS").value); fd.set("north", $("#bN").value);
    if (state.source === "upload") for (const [k, f] of Object.entries(state.files)) fd.set(k, f, f.name);
    return fd;
  }

  async function startRun() {
    showError(""); toggleSettings(false);
    document.body.classList.add("is-running");
    runLabel.textContent = "Starting…";
    try {
      const { run_id } = await api.json("/api/runs", { method: "POST", body: formData() });
      for (;;) {
        await sleep(550);
        const st = await api.json(`/api/runs/${run_id}`);
        if (st.status === "running") { runLabel.textContent = st.stage || "Working…"; continue; }
        if (st.status === "error") throw new Error(st.error || "The analysis failed.");
        await showResults(st);
        break;
      }
    } catch (err) {
      showError(err.message);
    } finally {
      document.body.classList.remove("is-running");
      runLabel.textContent = "Analyze damage";
    }
  }

  /* ------------------------------------------------------------------ results */
  async function showResults(st) {
    const { sites } = await api.json(`/api/runs/${st.run_id}/sites`);
    state.run = st; state.sites = sites; state.rendered = {}; state.selected = sites[0]?.rank ?? null; state.k = st.route_size || 8;
    $("#routeK").value = String(state.k); $("#routeKOut").textContent = String(state.k);

    const notes = $("#notes");
    notes.replaceChildren();
    if (st.demo) notes.append(h("p", { class: "note", text: "Demo data: a generated town with known damage, used to test the pipeline. It is not a real event." }));
    for (const w of st.warnings || []) notes.append(h("p", { class: "note", text: w }));
    notes.hidden = !notes.children.length;

    const s = st.summary || {};
    const figs = [
      ["buildings", "Sites analyzed", null], ["destroyed", "Destroyed", "d-destroyed"], ["major", "Major", "d-major"],
      ["minor", "Minor", "d-minor"], ["road_flags", "Road segments flagged", null], ["critical_facilities_hit", "Critical facilities hit", null],
    ];
    $("#figures").replaceChildren(...figs.map(([key, label, dot]) =>
      h("div", { class: "fig", role: "listitem" }, h("b", { text: String(s[key] ?? 0) }), h("span", {}, dot ? h("i", { class: "dot " + dot }) : null, label))));

    $("#home").hidden = true; $("#results").hidden = false;
    $("#tabs").hidden = false; $("#newRunBtn").hidden = false;
    selectView("map");
    window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
  }

  function goHome() {
    $("#results").hidden = true; $("#home").hidden = false;
    $("#tabs").hidden = true; $("#newRunBtn").hidden = true;
    window.scrollTo({ top: 0 });
  }
  $("#newRunBtn").addEventListener("click", goHome);
  $("#brand").addEventListener("click", (e) => { e.preventDefault(); if (state.run) goHome(); else window.scrollTo({ top: 0 }); });

  /* ---- tabs */
  const tabs = $$("#tabs .tab");
  $("#tabs").setAttribute("role", "tablist");
  tabs.forEach((t) => { t.setAttribute("role", "tab"); t.addEventListener("click", () => selectView(t.dataset.view)); });
  function selectView(name) {
    state.view = name;
    tabs.forEach((t) => { const on = t.dataset.view === name; t.classList.toggle("is-on", on); t.setAttribute("aria-selected", String(on)); });
    $$(".view").forEach((v) => { v.hidden = v.id !== "view-" + name; });
    if (!state.rendered[name]) { state.rendered[name] = true; renderers[name]?.(); }
    else if (name === "route") placeRouteSvg();
    else if (name === "why") renderWhy();
  }

  const img = (kind) => runUrl(`/images/${kind}.jpg`);

  const renderers = {
    compare() {
      const { image, alignment: a } = state.run;
      buildWipe($("#mainWipe"), img("pre"), img("post"), image.w / image.h);
      $("#alignCaption").textContent = a
        ? `Alignment is ${a.quality}: ${a.inliers} matched points, shift ${Number(a.shift_px).toFixed(1)} px, rotation ${Number(a.rotation_deg).toFixed(2)}°. Drag the line to compare.`
        : "";
    },

    map() {
      $("#overlayImg").src = img("overlay");
      const toggle = $("#heatToggle");
      state.heat = false;
      toggle.textContent = "Show change heat"; toggle.setAttribute("aria-pressed", "false");
      toggle.onclick = () => {
        state.heat = !state.heat;
        $("#overlayImg").src = img(state.heat ? "heat" : "overlay");
        toggle.textContent = state.heat ? "Show damage" : "Show change heat";
        toggle.setAttribute("aria-pressed", String(state.heat));
      };
      const top = state.sites.slice(0, 10);
      const list = $("#topList");
      list.replaceChildren(...top.map((s) =>
        h("li", {}, h("button", { type: "button", onclick: () => openWhy(s.rank) },
          h("span", { class: "rank", text: String(s.rank) }),
          h("span", {}, h("strong", { text: s.id }), h("small", {}, h("i", { class: "dot d-" + s.severity }), cap(s.severity) + (s.type === "road" ? ", road" : s.facility ? ", " + s.facility : ""))),
          h("span", { class: "pi", text: String(s.priority_index) })))));
      if (!top.length) list.append(h("li", { class: "empty", text: "No damage found with these thresholds." }));
    },

    list() {
      const chips = $("#tierChips");
      const defs = [["P1", "Urgent"], ["P2", "High"], ["P3", "Routine"]];
      chips.replaceChildren(...defs.map(([t, label]) => {
        const c = h("button", { class: "chip", type: "button", "aria-pressed": String(state.tiers.has(t)) }, `${t} ${label}`);
        c.addEventListener("click", () => {
          state.tiers.has(t) ? state.tiers.delete(t) : state.tiers.add(t);
          c.setAttribute("aria-pressed", String(state.tiers.has(t))); drawTable();
        });
        return c;
      }));
      const dl = (path, label) => h("a", { class: "btn btn-sm", "data-tone": "ink", href: runUrl("/exports/" + path), download: "" }, label);
      $("#downloads").replaceChildren(dl("csv", "CSV"), dl("geojson", "GeoJSON"), dl("kml", "KML for Google Earth"), dl("report", "Printable report"));
      drawTable();
    },

    route: () => loadRoute(),

    live() { $("#liveFrame").src = runUrl("/map"); },

    why: () => renderWhy(),

    accuracy: () => loadAccuracy(),
  };

  function drawTable() {
    const body = $("#siteTable tbody");
    const rows = state.sites.filter((s) => state.tiers.has(s.tier));
    if (!rows.length) {
      body.replaceChildren(h("tr", {}, h("td", { colspan: "9", class: "empty", text: "No sites in the selected tiers." })));
      return;
    }
    body.replaceChildren(...rows.map((s) => {
      const tr = h("tr", { tabindex: "0" },
        h("td", { class: "rk", text: String(s.rank) }),
        h("td", {}, s.id, s.type === "road" ? h("small", { text: " road" }) : null),
        h("td", {}, h("span", { class: "tier tier-" + s.tier, text: s.tier_name })),
        h("td", {}, h("span", { class: "sev" }, h("i", { class: "dot d-" + s.severity }), s.severity)),
        h("td", { class: "num", text: String(s.priority_index) }),
        h("td", { text: s.confidence }),
        h("td", { class: "gps", text: `${s.lat.toFixed(6)}, ${s.lon.toFixed(6)}` }),
        h("td", { text: s.facility || "" }),
        h("td", {}, isMapsUrl(s.maps_link) ? h("a", { href: s.maps_link, target: "_blank", rel: "noopener noreferrer", onclick: (e) => e.stopPropagation() }, "Open") : null));
      tr.addEventListener("click", () => openWhy(s.rank));
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") openWhy(s.rank); });
      return tr;
    }));
  }

  /* ---- route */
  let routeData = null;
  async function loadRoute() {
    try {
      routeData = await api.json(runUrl(`/route?k=${state.k}`));
    } catch (err) { $("#routeSummary").textContent = err.message; return; }
    const r = routeData;
    $("#routeSummary").textContent = r.stops.length
      ? `${r.stops.length} sites, about ${r.km} km from base camp.`
      : "There are no damaged sites to visit.";
    $("#stops").replaceChildren(...r.stops.map((s) =>
      h("li", {}, h("span", { class: "rank", text: String(s.visit_order) }),
        h("div", {}, h("strong", { text: s.id }), h("small", { text: `${cap(s.severity)}, ${s.tier_name.toLowerCase()}${s.facility ? ", near " + s.facility : ""}` })))));
    const link = $("#routeLink");
    link.hidden = !isMapsUrl(r.maps_url);
    if (!link.hidden) link.href = r.maps_url;
    const pic = $("#routeImg");
    if (pic.getAttribute("src") !== img("post")) pic.src = img("post");
    if (pic.complete) placeRouteSvg(); else pic.onload = placeRouteSvg;
    if (!pic._ro && "ResizeObserver" in window) { pic._ro = new ResizeObserver(placeRouteSvg); pic._ro.observe(pic); }
  }
  const SVGNS = "http://www.w3.org/2000/svg";
  function svgEl(name, attrs, text) {
    const el = document.createElementNS(SVGNS, name);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, String(v));
    if (text != null) el.textContent = text;
    return el;
  }
  function placeRouteSvg() {
    const pic = $("#routeImg"), svg = $("#routeSvg");
    if (!routeData || !pic.naturalWidth || !pic.clientWidth) return;
    const W = pic.naturalWidth, H = pic.naturalHeight;
    Object.assign(svg.style, { left: pic.offsetLeft + "px", top: pic.offsetTop + "px", width: pic.clientWidth + "px", height: pic.clientHeight + "px" });
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const scale = W / pic.clientWidth;               // keep markers the same size on screen
    const pts = routeData.stops.map((s) => s.px);
    const path = (routeData.base_px ? [routeData.base_px] : []).concat(pts).map((p) => `${p.x},${p.y}`).join(" ");
    const kids = [];
    if (pts.length) {
      kids.push(svgEl("polyline", { points: path, fill: "none", stroke: "#000", "stroke-width": 7 * scale, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      kids.push(svgEl("polyline", { points: path, fill: "none", stroke: "#bfe0ff", "stroke-width": 3.5 * scale, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    }
    if (routeData.base_px) {
      const b = routeData.base_px, a = 9 * scale;
      kids.push(svgEl("rect", { x: b.x - a, y: b.y - a, width: 2 * a, height: 2 * a, fill: "#000", stroke: "#fff", "stroke-width": 2 * scale, transform: `rotate(45 ${b.x} ${b.y})` }));
    }
    routeData.stops.forEach((s) => {
      kids.push(svgEl("circle", { cx: s.px.x, cy: s.px.y, r: 13 * scale, fill: "#bfe0ff", stroke: "#000", "stroke-width": 2.5 * scale }));
      kids.push(svgEl("text", { x: s.px.x, y: s.px.y, "text-anchor": "middle", "dominant-baseline": "central", "font-size": 13 * scale, "font-weight": 700, fill: "#000", "font-family": "sans-serif" }, s.visit_order));
    });
    svg.replaceChildren(...kids);
  }
  $("#routeK").addEventListener("input", (e) => { $("#routeKOut").textContent = e.target.value; });
  $("#routeK").addEventListener("change", (e) => { state.k = Number(e.target.value); loadRoute(); });
  window.addEventListener("resize", () => { if (state.view === "route") placeRouteSvg(); });

  /* ---- why this rank */
  function openWhy(rank) { state.selected = rank; selectView("why"); }
  function renderWhy() {
    const sel = $("#siteSelect");
    if (sel._run !== state.run.run_id) {
      sel._run = state.run.run_id;
      sel.replaceChildren(...state.sites.filter((s) => s.type !== "road").map((s) =>
        h("option", { value: String(s.rank) }, `#${s.rank}  ${s.id}  (${s.tier})`)));
      sel.onchange = () => { state.selected = Number(sel.value); renderWhy(); };
    }
    const site = state.sites.find((s) => s.rank === state.selected) || state.sites.find((s) => s.type !== "road");
    if (!site) { $("#whyTitle").textContent = "No damaged buildings to explain."; return; }
    sel.value = String(site.rank);
    $("#whyTitle").replaceChildren(
      h("strong", { text: site.id }),
      ` is ${site.severity}, ${site.tier_name.toLowerCase()} priority, ${site.confidence.toLowerCase()} confidence. GPS ${site.lat.toFixed(6)}, ${site.lon.toFixed(6)}.`);
    const crop = (kind) => runUrl(`/sites/${site.rank}/crop/${kind}.jpg`);
    $("#cropPre").src = crop("pre"); $("#cropPost").src = crop("post"); $("#cropHeat").src = crop("heat");
    const pct = (v) => Math.round(v * 100) + "%";
    const row = (a, b, key) => h("tr", { class: key ? "key" : "" }, h("td", { text: a }), h("td", { text: b }));
    $("#evidence tbody").replaceChildren(
      row("Share of the roof that changed a lot", pct(site.changed_fraction)),
      row("Structural dissimilarity (SSIM based)", site.dissimilarity.toFixed(2)),
      row("Edge and texture change", site.edge_change.toFixed(2)),
      row("Damage score", site.damage_score.toFixed(2), true),
      row("Facility importance", site.criticality.toFixed(1) + (site.facility ? ` (${site.facility})` : "")),
      row("Neighborhood damage share", pct(site.hotspot)),
      row(`Priority index = 100 × ${site.damage_score.toFixed(2)} × (1 + ${site.criticality.toFixed(1)}) × (1 + 0.5 × ${site.hotspot.toFixed(2)})`, String(site.priority_index), true));
  }

  /* ---- accuracy */
  async function loadAccuracy() {
    const body = $("#accuracyBody");
    let ev;
    try { ev = await api.json(runUrl("/evaluation")); } catch (err) { body.replaceChildren(h("p", { class: "error", text: err.message })); return; }
    if (!ev.available) {
      const input = h("input", { type: "file", accept: ".csv", class: "sr", id: "truthInput" });
      const msg = h("p", { class: "error", hidden: true, role: "alert" });
      const pick = h("label", { class: "btn", "data-tone": "ink", for: "truthInput" }, "Choose ground-truth CSV");
      input.addEventListener("change", async () => {
        if (!input.files[0]) return;
        const fd = new FormData(); fd.set("truth", input.files[0], input.files[0].name);
        try { await api.json(runUrl("/truth"), { method: "POST", body: fd }); loadAccuracy(); }
        catch (err) { msg.textContent = err.message; msg.hidden = false; }
      });
      body.replaceChildren(h("div", { class: "drop" },
        h("h2", { text: "Measure how accurate this is" }),
        h("p", { text: "Upload a CSV with the columns id and gt, where gt is none, minor, major or destroyed. You get accuracy, F1 and a confusion matrix." }),
        pick, input, msg));
      return;
    }
    const figs = [[Math.round(ev.accuracy * 100) + "%", "Exact severity accuracy"], [ev.macro_f1.toFixed(2), "Macro F1"],
      [ev.damaged_f1.toFixed(2), "Damaged or not, F1"], [Math.round(ev.top10_precision * 100) + "%", "Top 10 precision"]];
    const head = h("tr", {}, h("th", { text: "Truth, by prediction" }), ...ev.labels.map((l) => h("th", { text: cap(l) })));
    const max = Math.max(1, ...ev.confusion.flat());
    const rows = ev.confusion.map((r, i) => h("tr", {}, h("td", { text: cap(ev.labels[i]) }),
      ...r.map((v) => { const td = h("td", { text: String(v) }); td.style.background = `rgb(191 224 255 / ${(v / max).toFixed(2)})`; return td; })));
    body.replaceChildren(
      state.run.demo ? h("p", { class: "note", text: "Measured on synthetic data. This shows the pipeline works, not how accurate it is in the real world. Validate on xBD for a real number." }) : null,
      h("div", { class: "figures four", style: "margin-top:14px" }, figs.map(([v, l]) => h("div", { class: "fig" }, h("b", { text: v }), h("span", { text: l })))),
      h("div", { class: "table-wrap matrix" }, h("table", { class: "table" }, h("thead", {}, head), h("tbody", {}, rows))),
      h("p", { class: "caption", text: `Compared ${ev.n} buildings. Rows are the truth, columns are what the analyzer predicted.` }));
  }

  /* ------------------------------------------------------------------ go */
  openingSequence();
  updateSourceUi();
})();
