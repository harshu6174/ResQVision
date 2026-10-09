"""Geo-referencing: converting between image pixels and real GPS (lon/lat).

KEY IDEA: a satellite image is just a grid of pixels. To say "building #14 is
at 16.98N, 82.25E" we need a mapping pixel (col,row) -> (lon,lat). For small
areas an *affine* mapping (shift + scale + rotation) is accurate enough.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np

M_PER_DEG_LAT = 110_574.0


def m_per_deg_lon(lat_deg: float) -> float:
    return 111_320.0 * float(np.cos(np.radians(lat_deg)))


@dataclass
class GeoRef:
    M: np.ndarray            # 3x2 matrix: [col,row,1] @ M = [lon,lat]
    shape: tuple             # (height, width)

    # ---- constructors -------------------------------------------------
    @classmethod
    def from_gcps(cls, px, lonlat, shape):
        """Fit the mapping from ground-control points (pixel <-> lon/lat pairs)."""
        px = np.asarray(px, float); ll = np.asarray(lonlat, float)
        X = np.c_[px, np.ones(len(px))]
        M, *_ = np.linalg.lstsq(X, ll, rcond=None)
        return cls(M, tuple(shape))

    @classmethod
    def from_bounds(cls, west, south, east, north, shape):
        h, w = shape[:2]
        return cls.from_gcps([[0, 0], [w, 0], [0, h], [w, h]],
                             [[west, north], [east, north], [west, south], [east, south]], (h, w))

    @classmethod
    def from_raster(cls, path):
        import rasterio
        from rasterio.transform import xy
        from rasterio.warp import transform as warp_transform
        with rasterio.open(path) as ds:
            h, w = ds.height, ds.width
            if ds.crs is None:
                raise ValueError("Raster has no CRS/geo-referencing; supply bounds instead.")
            cols, rows = np.meshgrid(np.linspace(0, w, 5), np.linspace(0, h, 5))
            cols, rows = cols.ravel(), rows.ravel()
            xs, ys = xy(ds.transform, rows, cols, offset="ul")
            if ds.crs.to_epsg() != 4326:
                xs, ys = warp_transform(ds.crs, "EPSG:4326", xs, ys)
        return cls.from_gcps(np.c_[cols, rows], np.c_[xs, ys], (h, w))

    # ---- conversions --------------------------------------------------
    def px_to_lonlat(self, cols, rows):
        cols = np.asarray(cols, float); rows = np.asarray(rows, float)
        X = np.stack([cols, rows, np.ones_like(cols)], axis=-1)
        out = X @ self.M
        return out[..., 0], out[..., 1]

    def lonlat_to_px(self, lons, lats):
        lons = np.asarray(lons, float); lats = np.asarray(lats, float)
        A = self.M[:2]                      # 2x2
        d = np.stack([lons, lats], axis=-1) - self.M[2]
        cr = d @ np.linalg.inv(A)
        return cr[..., 0], cr[..., 1]

    @property
    def metres_per_px(self) -> float:
        lon0, lat0 = self.px_to_lonlat(0, 0)
        lon1, lat1 = self.px_to_lonlat(1, 0)
        lon2, lat2 = self.px_to_lonlat(0, 1)
        dx = np.hypot((lon1 - lon0) * m_per_deg_lon(lat0), (lat1 - lat0) * M_PER_DEG_LAT)
        dy = np.hypot((lon2 - lon0) * m_per_deg_lon(lat0), (lat2 - lat0) * M_PER_DEG_LAT)
        return float((dx + dy) / 2)


def poly_to_px(poly, georef: GeoRef):
    """Shapely polygon/linestring in lon/lat -> same geometry in pixel coords."""
    import shapely
    return shapely.transform(poly, lambda c: np.stack(georef.lonlat_to_px(c[:, 0], c[:, 1]), axis=-1))


def haversine_m(lon1, lat1, lon2, lat2):
    R = 6_371_000.0
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1; dl = np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def maps_link(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps?q={lat:.6f},{lon:.6f}"
