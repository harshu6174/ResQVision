"""Step 1 of processing: IMAGE ALIGNMENT (registration).

Two satellite passes never line up perfectly (different viewing angle, tiny
orbit differences). If we compare un-aligned images, every edge looks like
'damage'. We find matching feature points in both images (ORB), then solve
for the shift+rotation+scale that lays the post image on the pre image.
"""
from __future__ import annotations
import cv2
import numpy as np


def align_post_to_pre(pre: np.ndarray, post: np.ndarray):
    h, w = pre.shape[:2]
    if post.shape[:2] != (h, w):
        post = cv2.resize(post, (w, h), interpolation=cv2.INTER_AREA)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    g1 = clahe.apply(cv2.cvtColor(pre, cv2.COLOR_RGB2GRAY))
    g2 = clahe.apply(cv2.cvtColor(post, cv2.COLOR_RGB2GRAY))
    orb = cv2.ORB_create(6000)
    k1, d1 = orb.detectAndCompute(g1, None)
    k2, d2 = orb.detectAndCompute(g2, None)
    info = {"matches": 0, "inliers": 0, "shift_px": 0.0, "rotation_deg": 0.0, "quality": "failed"}
    if d1 is None or d2 is None or len(k1) < 12 or len(k2) < 12:
        return post, info
    matches = sorted(cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(d2, d1),
                     key=lambda m: m.distance)[:1000]
    info["matches"] = len(matches)
    if len(matches) < 12:
        return post, info
    src = np.float32([k2[m.queryIdx].pt for m in matches])
    dst = np.float32([k1[m.trainIdx].pt for m in matches])
    M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=2.5)
    if M is None:
        return post, info
    n_inl = int(inl.sum())
    info.update(inliers=n_inl,
                shift_px=float(np.hypot(M[0, 2], M[1, 2])),
                rotation_deg=float(np.degrees(np.arctan2(M[1, 0], M[0, 0]))))
    info["quality"] = "good" if n_inl >= 40 else "weak" if n_inl >= 15 else "failed"
    if info["quality"] == "failed":
        return post, info
    warped = cv2.warpAffine(post, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    return warped, info
