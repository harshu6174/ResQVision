"""MEASURE the system. Works with any ground truth (our synthetic data or real xBD labels).

Run:  python -m damage_analyzer.evaluate demo_data
"""
from __future__ import annotations
import itertools, sys
import numpy as np
import pandas as pd
from .scoring import SEVERITIES, Config, severity_from_score


def confusion(df, gt_col="gt", pred_col="severity"):
    return pd.crosstab(df[gt_col], df[pred_col]).reindex(index=SEVERITIES, columns=SEVERITIES, fill_value=0)


def macro_f1(df, gt_col="gt", pred_col="severity"):
    f1s = []
    for c in SEVERITIES:
        tp = ((df[gt_col] == c) & (df[pred_col] == c)).sum(); fp = ((df[gt_col] != c) & (df[pred_col] == c)).sum()
        fn = ((df[gt_col] == c) & (df[pred_col] != c)).sum()
        p = tp / (tp + fp) if tp + fp else 0; r = tp / (tp + fn) if tp + fn else 0
        f1s.append(2 * p * r / (p + r) if p + r else 0)
    return float(np.mean(f1s))


def damaged_vs_not(df, gt_col="gt", pred_col="severity"):
    g = df[gt_col] != "none"; p = df[pred_col] != "none"
    tp, fp, fn = (g & p).sum(), (~g & p).sum(), (g & ~p).sum()
    prec = tp / (tp + fp) if tp + fp else 0; rec = tp / (tp + fn) if tp + fn else 0
    return dict(precision=round(float(prec), 3), recall=round(float(rec), 3),
                f1=round(float(2 * prec * rec / (prec + rec)) if prec + rec else 0, 3))


def top_k_precision(df, k=10, gt_col="gt"):
    """Of the k sites we say to inspect FIRST, how many are truly major/destroyed?"""
    top = df.sort_values("priority_index", ascending=False).head(k)
    return float(top[gt_col].isin(["major", "destroyed"]).mean())


def calibrate_thresholds(df, gt_col="gt", grid=None):
    """Search the three score thresholds that maximise macro-F1 on LABELLED data."""
    grid = grid or np.round(np.arange(0.10, 0.95, 0.02), 2)
    best = (-1, None)
    for a, b, c in itertools.combinations(grid, 3):
        cfg = Config(minor=a, major=b, destroyed=c)
        tmp = df.assign(severity=[severity_from_score(s, cfg) for s in df.damage_score])
        f = macro_f1(tmp, gt_col)
        if f > best[0]: best = (f, (a, b, c))
    return best


def report(res, gt_df):
    m = res.df[res.df.type == "building"].merge(gt_df, on="id")
    out = {"n": len(m), "accuracy": round(float((m.severity == m["gt"]).mean()), 3),
           "macro_f1": round(macro_f1(m), 3), "damaged_vs_not": damaged_vs_not(m),
           "top10_precision": top_k_precision(m, 10), "confusion": confusion(m)}
    return out, m


if __name__ == "__main__":
    from .io_utils import load_image, load_footprints, load_pois, load_roads
    from .pipeline import analyze
    d = sys.argv[1].rstrip("/") + "/"
    pre, g = load_image(d + "pre.tif"); post, _ = load_image(d + "post.tif")
    res = analyze(pre, post, g, load_footprints(d + "footprints.geojson"), load_pois(d + "pois.geojson"), load_roads(d + "roads.geojson"))
    rep, m = report(res, pd.read_csv(d + "ground_truth.csv"))
    for k, v in rep.items(): print(f"\n{k}:\n{v}" if k == "confusion" else f"{k}: {v}")
    f, th = calibrate_thresholds(m)
    print(f"\nbest thresholds on this data (minor, major, destroyed) = {th}  macro-F1={f:.3f}")
