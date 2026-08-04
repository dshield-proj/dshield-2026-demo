#!/usr/bin/env python3
"""DShield burned-area fire-watch console — local data server.

Serves the console page and a JSON API that reads the tasking configs and
CYGNSS burned-area detection CSVs live from the local drive on every request.
New/removed day folders are picked up automatically; parsed CSVs are cached
in memory and invalidated by file mtime.

Stdlib only — no dependencies.

Usage:
    python3 server.py                    # http://localhost:8000
    PORT=8080 python3 server.py
    FIRE_TOKEN=mysecret python3 server.py   # require ?token=mysecret (for tunnels)

Environment:
    FIRE_CONFIG_ROOT  tasking configs (default: ../dshield-demo-configuration/burned-area-config)
    FIRE_DETECT_ROOT  detection CSVs  (default: ../burned-area/output)
    FIRE_DANGER_ROOT  WFPI danger zips (default: ../fire-danger/output)
    FIRE_SOIL_ROOT    soil-moisture GeoTIFFs (default: ../soil-moisture/output)
    FIRE_SOIL_CONFIG_ROOT  sm_areas.json boxes (default: ../dshield-demo-configuration/soil-moisture-config)
    FIRE_PLAN_ROOT    RawIF plan CSVs (default: ../planner/output)
    FIRE_ORBIT_ROOT   specular trajectories (default: ../orbits-actual)
"""
import array
import csv
import hashlib
import json
import math
import os
import re
import struct
import sys
import threading
import zipfile
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

csv.field_size_limit(10 ** 7)

BASE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.dirname(BASE)
CONFIG_ROOT = os.environ.get(
    "FIRE_CONFIG_ROOT", os.path.join(DEMO, "dshield-demo-configuration", "burned-area-config"))
DETECT_ROOT = os.environ.get("FIRE_DETECT_ROOT", os.path.join(DEMO, "burned-area", "output"))
DANGER_ROOT = os.environ.get("FIRE_DANGER_ROOT", os.path.join(DEMO, "fire-danger", "output"))
SOIL_ROOT = os.environ.get("FIRE_SOIL_ROOT", os.path.join(DEMO, "soil-moisture", "output"))
SOIL_CONFIG_ROOT = os.environ.get(
    "FIRE_SOIL_CONFIG_ROOT",
    os.path.join(DEMO, "dshield-demo-configuration", "soil-moisture-config"))
PLAN_ROOT = os.environ.get("FIRE_PLAN_ROOT", os.path.join(DEMO, "planner", "output"))
ORBIT_ROOT = os.environ.get("FIRE_ORBIT_ROOT", os.path.join(DEMO, "orbits-actual"))
TOKEN = os.environ.get("FIRE_TOKEN", "")
PORT = int(os.environ.get("PORT", "8000"))

DAY_RE = re.compile(r"^\d{8}$")
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/conus.json": ("conus.json", "application/json"),
}

REGION_NAMES = {"AZ": "Southwest", "FL": "Southeast", "CA": "West Coast"}


def region_of(lat, lon):
    if lon < -114:
        return "CA"
    if lon < -100:
        return "AZ"
    return "FL"


# ---------------------------------------------------------------- caches
_lock = threading.Lock()
_det_cache = {}  # csv path -> (mtime, payload dict)


def detections_path(day, fire):
    return os.path.join(DETECT_ROOT, day, f"burned_{fire}.csv")


def load_detections(day, fire):
    """Compact detection arrays for one fire-day, cached by file mtime."""
    path = detections_path(day, fire)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    with _lock:
        hit = _det_cache.get(path)
        if hit and hit[0] == mtime:
            return hit[1]
    lat, lon, y, u = [], [], [], []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                la = float(row["sp_lat"])
                lo = float(row["sp_lon"])
                yp = int(float(row["y_pred"]))
                un = float(row.get("y_uncert", "") or "nan")
            except (ValueError, KeyError):
                continue
            if not (-90 <= la <= 90 and -180 <= lo <= 180):
                continue
            lat.append(round(la, 4))
            lon.append(round(lo, 4))
            y.append(yp)
            u.append(round(un, 3) if un == un else None)
    payload = {"day": day, "fire": fire, "n": len(y),
               "n_burned": sum(y), "lat": lat, "lon": lon, "y": y, "u": u}
    with _lock:
        _det_cache[path] = (mtime, payload)
    return payload


# ------------------------------------------------ fire danger (WFPI Day-1)
# fire-danger/output/YYYYMMDD/wfpi_YYYYMMDD_Day1.zip holds one 8-bit palette
# GeoTIFF (4587×2889, uncompressed, 1 km USGS CONUS Lambert Azimuthal
# Equal-Area sphere grid). Values 0–150 are the fire-potential index; 249–254
# are land-cover specials (barren/urban/ag/snow/water); 255 nodata. Rendered
# here to indexed PNGs — warped into the console's Albers map space
# (view=conus) or a plain lon/lat rectangle (view=geo) — using the palette
# embedded in the tif, with specials/nodata transparent.

_D2R = math.pi / 180.0

# Console map projection — must match albersFactory() in static/index.html.
_AB_N = (math.sin(29.5 * _D2R) + math.sin(45.5 * _D2R)) / 2
_AB_C = math.cos(29.5 * _D2R) ** 2 + 2 * _AB_N * math.sin(29.5 * _D2R)
_AB_RHO0 = math.sqrt(_AB_C - 2 * _AB_N * math.sin(23 * _D2R)) / _AB_N


def albers_fwd(lon, lat):
    rho = math.sqrt(max(0.0, _AB_C - 2 * _AB_N * math.sin(lat * _D2R))) / _AB_N
    th = _AB_N * (lon + 96.0) * _D2R
    return rho * math.sin(th), _AB_RHO0 - rho * math.cos(th)


def albers_inv(x, y):
    dy = _AB_RHO0 - y
    rho = math.hypot(x, dy)
    lat = math.asin(max(-1.0, min(1.0, (_AB_C - (rho * _AB_N) ** 2) / (2 * _AB_N)))) / _D2R
    return -96.0 + math.atan2(x, dy) / _AB_N / _D2R, lat


# Raster projection: LAEA on the ARC/INFO sphere, center 45N 100W (GeoTIFF keys).
_R_SPH = 6370997.0
_SIN45 = math.sin(45 * _D2R)
_COS45 = math.cos(45 * _D2R)


def laea_grid(lon, lat, geo):
    """lon/lat degrees -> fractional (col, row) in the raster grid."""
    lam = (lon + 100.0) * _D2R
    phi = lat * _D2R
    den = 1.0 + _SIN45 * math.sin(phi) + _COS45 * math.cos(phi) * math.cos(lam)
    if den < 1e-12:
        return -1e9, -1e9
    k = _R_SPH * math.sqrt(2.0 / den)
    x = k * math.cos(phi) * math.sin(lam)
    y = k * (_COS45 * math.sin(phi) - _SIN45 * math.cos(phi) * math.cos(lam))
    tx, ty, psx, psy = geo
    return (x - tx) / psx, (ty - y) / psy


def laea_inv(xm, ym):
    """LAEA meters -> (lon, lat) degrees."""
    rho = math.hypot(xm, ym)
    if rho < 1e-9:
        return -100.0, 45.0
    c = 2.0 * math.asin(min(1.0, rho / (2.0 * _R_SPH)))
    lat = math.asin(max(-1.0, min(1.0, math.cos(c) * _SIN45 + ym * math.sin(c) * _COS45 / rho))) / _D2R
    lon = -100.0 + math.atan2(xm * math.sin(c), rho * _COS45 * math.cos(c) - ym * _SIN45 * math.sin(c)) / _D2R
    return lon, lat


_TIF_TYPE = {1: ("B", 1), 3: ("H", 2), 4: ("I", 4), 12: ("d", 8)}


def _parse_tif(buf):
    """Minimal reader for the danger GeoTIFFs (uncompressed 8-bit, strips)."""
    bo = {b"II": "<", b"MM": ">"}.get(buf[:2])
    if not bo:
        raise ValueError("not a TIFF")
    (ifd,) = struct.unpack(bo + "I", buf[4:8])
    (ntags,) = struct.unpack(bo + "H", buf[ifd:ifd + 2])
    tags = {}
    for i in range(ntags):
        ent = buf[ifd + 2 + i * 12: ifd + 14 + i * 12]
        tag, typ, cnt = struct.unpack(bo + "HHI", ent[:8])
        if typ not in _TIF_TYPE:
            continue
        fmt, size = _TIF_TYPE[typ]
        total = size * cnt
        raw = ent[8:8 + total] if total <= 4 else buf[struct.unpack(bo + "I", ent[8:12])[0]:][:total]
        tags[tag] = struct.unpack(bo + fmt * cnt, raw)
    w, h = tags[256][0], tags[257][0]
    if tags.get(259, (1,))[0] != 1 or tags.get(258, (8,))[0] != 8:
        raise ValueError("unsupported TIFF layout (need uncompressed 8-bit)")
    grid = b"".join(buf[o:o + c] for o, c in zip(tags[273], tags[279]))
    if len(grid) != w * h:
        raise ValueError("truncated raster")
    cm = tags.get(320)
    if cm:
        pal = bytearray(768)
        for v in range(256):
            pal[3 * v] = cm[v] >> 8
            pal[3 * v + 1] = cm[256 + v] >> 8
            pal[3 * v + 2] = cm[512 + v] >> 8
        pal = bytes(pal)
    else:
        pal = bytes(x for v in range(256) for x in (v, v, v))
    scale = tags.get(33550, (1000.0, 1000.0, 0.0))
    tie = tags.get(33922, (0.0,) * 6)
    geo = (tie[3] - tie[0] * scale[0], tie[4] + tie[1] * scale[1], scale[0], scale[1])
    return {"w": w, "h": h, "grid": grid, "pal": pal, "geo": geo}


def danger_zip_path(day):
    return os.path.join(DANGER_ROOT, day, "wfpi_%s_Day1.zip" % day)


def danger_mtime(day):
    try:
        return int(os.path.getmtime(danger_zip_path(day)))
    except OSError:
        return None


_GRID_MAX = 4
_grid_cache = {}   # day -> (mtime, parsed tif dict)


def load_danger_grid(day):
    mtime = danger_mtime(day)
    if mtime is None:
        return None
    with _lock:
        hit = _grid_cache.get(day)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        with zipfile.ZipFile(danger_zip_path(day)) as z:
            name = next((n for n in z.namelist() if n.lower().endswith(".tif")), None)
            if not name:
                return None
            tif = _parse_tif(z.read(name))
    except (OSError, ValueError, zipfile.BadZipFile, struct.error, KeyError) as e:
        print(f"[danger] {day}: cannot read raster: {e}", file=sys.stderr)
        return None
    tif["mtime"] = mtime
    with _lock:
        _grid_cache[day] = (mtime, tif)
        while len(_grid_cache) > _GRID_MAX:
            _grid_cache.pop(next(k for k in _grid_cache if k != day))
    return tif


_rect_cache = {}


def danger_albers_rect(tif):
    """Raster extent as a rect [x0, y0, x1, y1] in console Albers units."""
    key = (tif["w"], tif["h"], tif["geo"])
    hit = _rect_cache.get(key)
    if hit:
        return hit
    w, h = tif["w"], tif["h"]
    tx, ty, psx, psy = tif["geo"]
    xs, ys = [], []
    step = max(1, w // 48)
    edge = [(c, 0) for c in range(0, w + 1, step)] + [(c, h) for c in range(0, w + 1, step)] \
        + [(0, r) for r in range(0, h + 1, step)] + [(w, r) for r in range(0, h + 1, step)]
    for col, row in edge:
        lon, lat = laea_inv(tx + col * psx, ty - row * psy)
        ax, ay = albers_fwd(lon, lat)
        xs.append(ax)
        ys.append(ay)
    rect = (min(xs), min(ys), max(xs), max(ys))
    _rect_cache[key] = rect
    return rect


def _warp_idx(W, H, to_src, w, h):
    """Index map output pixel -> raster offset (-1 outside), via an exact
    projection chain on a coarse node grid + bilinear interpolation between."""
    G = 8
    nx, ny = W // G + 2, H // G + 2
    ncol = [[0.0] * nx for _ in range(ny)]
    nrow = [[0.0] * nx for _ in range(ny)]
    for j in range(ny):
        cj, rj = ncol[j], nrow[j]
        for i in range(nx):
            cj[i], rj[i] = to_src(i * G, j * G)
    idx = array.array("l", [-1]) * (W * H)
    i_of = [px // G for px in range(W)]
    f_of = [(px % G) / G for px in range(W)]
    k = 0
    for py in range(H):
        j, ty = py // G, (py % G) / G
        sy = 1.0 - ty
        cA, cB, rA, rB = ncol[j], ncol[j + 1], nrow[j], nrow[j + 1]
        for px in range(W):
            i = i_of[px]
            t = f_of[px]
            s = 1.0 - t
            colf = (cA[i] * s + cA[i + 1] * t) * sy + (cB[i] * s + cB[i + 1] * t) * ty
            if 0.0 <= colf < w:
                rowf = (rA[i] * s + rA[i + 1] * t) * sy + (rB[i] * s + rB[i + 1] * t) * ty
                if 0.0 <= rowf < h:
                    idx[k] = int(rowf) * w + int(colf)
            k += 1
    return idx


# Palette alpha: index values 0–150 opaque, land-cover specials & nodata clear.
_ALPHA = bytes(255 if v <= 150 else 0 for v in range(256))


def _png_chunk(typ, payload):
    return struct.pack(">I", len(payload)) + typ + payload \
        + struct.pack(">I", zlib.crc32(typ + payload) & 0xFFFFFFFF)


def _png_indexed(w, h, pix, pal):
    raw = b"".join(b"\x00" + pix[y * w:(y + 1) * w] for y in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 3, 0, 0, 0))
            + _png_chunk(b"PLTE", pal)
            + _png_chunk(b"tRNS", _ALPHA)
            + _png_chunk(b"IDAT", zlib.compress(raw, 6))
            + _png_chunk(b"IEND", b""))


CONUS_PNG_W = 1104
_idx_cache = {}    # view geometry -> array of raster offsets (day-independent)
_png_cache = {}    # (day, mtime, view, bbox, width) -> png bytes


def danger_png(day, view, bbox=None, width=600):
    mtime = danger_mtime(day)
    if mtime is None:
        return None
    pkey = (day, mtime, view, bbox, width)
    with _lock:
        hit = _png_cache.get(pkey)
    if hit:
        return hit
    tif = load_danger_grid(day)
    if tif is None:
        return None
    w, h, geo = tif["w"], tif["h"], tif["geo"]
    if view == "conus":
        x0, y0, x1, y1 = danger_albers_rect(tif)
        W = CONUS_PNG_W
        H = max(1, round(W * (y1 - y0) / (x1 - x0)))
        ikey = ("conus", w, h, geo, W, H)

        def to_src(px, py):
            lon, lat = albers_inv(x0 + (px + 0.5) * (x1 - x0) / W,
                                  y1 - (py + 0.5) * (y1 - y0) / H)
            return laea_grid(lon, lat, geo)
    else:
        lon_a, lon_b, lat_a, lat_b = bbox
        W = width
        H = max(1, min(2400, round(
            W * (lat_b - lat_a) / ((lon_b - lon_a) * math.cos((lat_a + lat_b) / 2 * _D2R)))))
        ikey = ("geo", w, h, geo, bbox, W, H)

        def to_src(px, py):
            return laea_grid(lon_a + (px + 0.5) * (lon_b - lon_a) / W,
                             lat_b - (py + 0.5) * (lat_b - lat_a) / H, geo)

    with _lock:
        idx = _idx_cache.get(ikey)
    if idx is None:
        idx = _warp_idx(W, H, to_src, w, h)
        with _lock:
            _idx_cache[ikey] = idx
    lut = tif["grid"] + b"\xff"          # idx -1 -> sentinel nodata byte
    pix = bytes(map(lut.__getitem__, idx))
    png = _png_indexed(W, H, pix, tif["pal"])
    with _lock:
        _png_cache[pkey] = png
        while len(_png_cache) > 64:
            _png_cache.pop(next(iter(_png_cache)))
    return png


_sample_cache = {}  # (day, mtime, box) -> mean index or None


def danger_box_mean(day, lat_l, lat_u, lon_l, lon_u):
    """Mean fire-potential index over a fire box (specials excluded)."""
    mtime = danger_mtime(day)
    if mtime is None:
        return None
    key = (day, mtime, round(lat_l, 4), round(lat_u, 4), round(lon_l, 4), round(lon_u, 4))
    with _lock:
        if key in _sample_cache:
            return _sample_cache[key]
    tif = load_danger_grid(day)
    val = None
    if tif:
        w, h, grid, geo = tif["w"], tif["h"], tif["grid"], tif["geo"]
        tot = cnt = 0
        n = 24
        for i in range(n):
            lat = lat_l + (i + 0.5) * (lat_u - lat_l) / n
            for j in range(n):
                col, row = laea_grid(lon_l + (j + 0.5) * (lon_u - lon_l) / n, lat, geo)
                if 0 <= col < w and 0 <= row < h:
                    v = grid[int(row) * w + int(col)]
                    if v <= 150:
                        tot += v
                        cnt += 1
        if cnt:
            val = round(tot / cnt)
    with _lock:
        _sample_cache[key] = val
    return val


def danger_legend(pal):
    """Contiguous same-color value bins over 0–150: [[lo, hi, [r,g,b]], ...]."""
    bins = []
    for v in range(151):
        rgb = [pal[3 * v], pal[3 * v + 1], pal[3 * v + 2]]
        if bins and bins[-1][2] == rgb:
            bins[-1][1] = v
        else:
            bins.append([v, v, rgb])
    return bins


def danger_bundle_info(days_ids):
    dmap = {}
    for day in days_ids:
        m = danger_mtime(day)
        if m is not None:
            dmap[day] = m
    if not dmap:
        return None
    tif = load_danger_grid(sorted(dmap)[0])
    if tif is None:
        return None
    return {
        "product": "WFPI Day-1",
        "days": dmap,
        "rect": list(danger_albers_rect(tif)),
        "legend": danger_legend(tif["pal"]),
    }


# ------------------------------------------------------ soil moisture
# soil-moisture/output/YYYYMMDD/soil_moisture_area_id_N.tif — one 32-bit IEEE
# float GeoTIFF per fixed monitoring area, DEFLATE-compressed and tiled, on the
# SAME USGS CONUS LAEA-sphere 1 km grid as the WFPI danger rasters (so the same
# laea_grid/laea_inv/_warp_idx machinery re-projects it). NODATA (-9999) marks
# pixels outside the retrieved footprint. Rendered here to indexed PNGs with a
# dry→wet sequential colormap (nodata transparent); the bundle also carries each
# area's daily area-mean series and the shared value domain/units.

_SOIL_LABELS = {"NM": "New Mexico", "TX_panhandle": "Texas panhandle"}
_SOIL_K = 32
# dry (pale sand) -> wet (deep blue) — sequential, perceptually ordered.
_SOIL_ANCHORS = [
    (232, 220, 192), (201, 211, 166), (143, 198, 172),
    (79, 176, 182), (43, 138, 166), (31, 95, 139),
]


def soil_colormap(k=_SOIL_K):
    n = len(_SOIL_ANCHORS) - 1
    out = []
    for i in range(k):
        t = i / (k - 1) * n if k > 1 else 0.0
        lo = min(int(t), n - 1)
        f = t - lo
        a, b = _SOIL_ANCHORS[lo], _SOIL_ANCHORS[lo + 1]
        out.append([round(a[j] + (b[j] - a[j]) * f) for j in range(3)])
    return out


def soil_palette(k=_SOIL_K):
    pal = bytearray(768)
    for i, c in enumerate(soil_colormap(k)):
        pal[3 * i:3 * i + 3] = bytes(c)
    return bytes(pal)


def soil_tif_path(day, area_id):
    return os.path.join(SOIL_ROOT, day, "soil_moisture_area_id_%d.tif" % area_id)


def soil_mtime(day, area_id):
    try:
        return int(os.path.getmtime(soil_tif_path(day, area_id)))
    except OSError:
        return None


def list_soil_days():
    try:
        entries = sorted(e for e in os.listdir(SOIL_ROOT) if DAY_RE.match(e))
    except OSError:
        return []
    out = []
    for d in entries:
        dd = os.path.join(SOIL_ROOT, d)
        try:
            if any(n.startswith("soil_moisture_area_id_") and n.endswith(".tif")
                   for n in os.listdir(dd)):
                out.append(d)
        except OSError:
            pass
    return out


def list_soil_config_days():
    try:
        return sorted(e for e in os.listdir(SOIL_CONFIG_ROOT) if DAY_RE.match(e)
                      and os.path.isfile(os.path.join(SOIL_CONFIG_ROOT, e, "sm_areas.json")))
    except OSError:
        return []


def load_soil_areas():
    """Fixed monitoring areas from the newest sm_areas.json (boxes are constant)."""
    days = list_soil_config_days()
    if not days:
        return []
    try:
        with open(os.path.join(SOIL_CONFIG_ROOT, days[-1], "sm_areas.json")) as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return []
    areas = []
    for a in cfg:
        lat_l, lat_u = sorted((a["lat_lower"], a["lat_upper"]))
        lon_l, lon_u = sorted((a["lon_lower"], a["lon_upper"]))
        areas.append({"area_id": a["area_id"], "name": a["name"],
                      "lat_l": lat_l, "lat_u": lat_u, "lon_l": lon_l, "lon_u": lon_u})
    areas.sort(key=lambda x: x["area_id"])
    return areas


def soil_day_token(day, areas):
    ms = [m for a in areas if (m := soil_mtime(day, a["area_id"])) is not None]
    return max(ms) if ms else None


def _soil_area_pub(a):
    clat = (a["lat_l"] + a["lat_u"]) / 2
    clon = (a["lon_l"] + a["lon_u"]) / 2
    reg = region_of(clat, clon)
    return {
        "id": a["area_id"], "name": a["name"],
        "label": _SOIL_LABELS.get(a["name"], a["name"].replace("_", " ")),
        "region": reg, "region_name": REGION_NAMES[reg],
        "lat_l": round(a["lat_l"], 4), "lat_u": round(a["lat_u"], 4),
        "lon_l": round(a["lon_l"], 4), "lon_u": round(a["lon_u"], 4),
        "clat": round(clat, 4), "clon": round(clon, 4),
    }


_soil_grid_cache = {}   # (day, area_id) -> (mtime, grid dict)
_SOIL_GRID_MAX = 8
_NATIVE_LE = sys.byteorder == "little"


def _parse_soil_tif(buf):
    """Reader for the soil GeoTIFFs (32-bit float, deflate, tiled or stripped)."""
    bo = {b"II": "<", b"MM": ">"}.get(buf[:2])
    if not bo:
        raise ValueError("not a TIFF")
    (ifd,) = struct.unpack(bo + "I", buf[4:8])
    (ntags,) = struct.unpack(bo + "H", buf[ifd:ifd + 2])
    tags, ascii_tags = {}, {}
    for i in range(ntags):
        ent = buf[ifd + 2 + i * 12: ifd + 14 + i * 12]
        tag, typ, cnt = struct.unpack(bo + "HHI", ent[:8])
        if typ == 2:
            raw = ent[8:8 + cnt] if cnt <= 4 else buf[struct.unpack(bo + "I", ent[8:12])[0]:][:cnt]
            ascii_tags[tag] = raw
            continue
        if typ not in _TIF_TYPE:
            continue
        fmt, size = _TIF_TYPE[typ]
        total = size * cnt
        raw = ent[8:8 + total] if total <= 4 else buf[struct.unpack(bo + "I", ent[8:12])[0]:][:total]
        tags[tag] = struct.unpack(bo + fmt * cnt, raw)
    w, h = tags[256][0], tags[257][0]
    if tags.get(258, (0,))[0] != 32 or tags.get(339, (1,))[0] != 3:
        raise ValueError("expected 32-bit IEEE-float soil raster")
    comp = tags.get(259, (1,))[0]
    nodata = None
    if 42113 in ascii_tags:
        try:
            nodata = float(ascii_tags[42113].split(b"\x00")[0] or b"nan")
        except ValueError:
            nodata = None
    scale = tags.get(33550, (1000.0, 1000.0, 0.0))
    tie = tags.get(33922, (0.0,) * 6)
    geo = (tie[3] - tie[0] * scale[0], tie[4] + tie[1] * scale[1], scale[0], scale[1])

    def _decompress(raw):
        if comp == 8:
            return zlib.decompress(raw)
        if comp == 1:
            return raw
        raise ValueError("unsupported compression %d" % comp)

    vals = array.array("f", bytes(4 * w * h))
    if 322 in tags:                      # tiled
        tw, th = tags[322][0], tags[323][0]
        across = (w + tw - 1) // tw
        for ti, (off, cnt) in enumerate(zip(tags[324], tags[325])):
            tvals = struct.unpack(bo + "f" * (tw * th), _decompress(buf[off:off + cnt])[:tw * th * 4])
            tx, ty = (ti % across) * tw, (ti // across) * th
            cols = min(tw, w - tx)
            for ly in range(th):
                gy = ty + ly
                if gy >= h:
                    break
                srow = ly * tw
                grow = gy * w + tx
                vals[grow:grow + cols] = array.array("f", tvals[srow:srow + cols])
    else:                                # stripped
        data = _decompress(b"".join(buf[o:o + c] for o, c in zip(tags[273], tags[279])))
        vals = array.array("f", data[:4 * w * h])
        if (bo == "<") != _NATIVE_LE:
            vals.byteswap()

    if nodata is not None:
        nan = float("nan")
        for i, v in enumerate(vals):
            if v == nodata or v != v or abs(v) > 1e30:
                vals[i] = nan
    return {"w": w, "h": h, "geo": geo, "vals": vals, "nodata": nodata}


def load_soil_grid(day, area_id):
    path = soil_tif_path(day, area_id)
    try:
        mtime = int(os.path.getmtime(path))
    except OSError:
        return None
    key = (day, area_id)
    with _lock:
        hit = _soil_grid_cache.get(key)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        with open(path, "rb") as fh:
            grid = _parse_soil_tif(fh.read())
    except (OSError, ValueError, struct.error, zlib.error, KeyError) as e:
        print(f"[soil] {day}/{area_id}: cannot read raster: {e}", file=sys.stderr)
        return None
    grid["mtime"] = mtime
    with _lock:
        _soil_grid_cache[key] = (mtime, grid)
        while len(_soil_grid_cache) > _SOIL_GRID_MAX:
            _soil_grid_cache.pop(next(k for k in _soil_grid_cache if k != key))
    return grid


def soil_area_mean(day, area_id):
    """Area-mean soil moisture over valid (non-nodata) pixels, or None."""
    g = load_soil_grid(day, area_id)
    if not g:
        return None
    tot = 0.0
    cnt = 0
    for v in g["vals"]:
        if v == v:
            tot += v
            cnt += 1
    return round(tot / cnt, 4) if cnt else None


_soil_domain_cache = {}


def soil_domain():
    """Shared colormap domain: robust 2–98th percentile over all valid pixels."""
    areas = load_soil_areas()
    days = list_soil_days()
    fp = tuple((d, a["area_id"], soil_mtime(d, a["area_id"])) for d in days for a in areas)
    with _lock:
        hit = _soil_domain_cache.get("v")
    if hit and hit[0] == fp:
        return hit[1]
    vals = []
    for d in days:
        for a in areas:
            g = load_soil_grid(d, a["area_id"])
            if g:
                vals.extend(v for v in g["vals"] if v == v)
    if vals:
        vals.sort()
        lo = vals[int(0.02 * (len(vals) - 1))]
        hi = vals[int(0.98 * (len(vals) - 1))]
        dom = (lo, hi if hi > lo else lo + 1e-6)
    else:
        dom = (0.0, 1.0)
    with _lock:
        _soil_domain_cache["v"] = (fp, dom)
    return dom


def soil_units(dom):
    return "m³/m³" if dom[1] <= 1.5 else ("%" if dom[1] <= 100 else "")


_soil_q_cache = {}


def soil_quantize(day, area_id, grid, dom, k):
    """Per-pixel colormap index (0..k-1); nodata -> index k. Cached by mtime+domain."""
    key = (day, area_id, grid["mtime"], round(dom[0], 6), round(dom[1], 6), k)
    with _lock:
        hit = _soil_q_cache.get(key)
    if hit:
        return hit
    lo, hi = dom
    span = (hi - lo) or 1.0
    km1 = k - 1
    out = bytearray(len(grid["vals"]))
    for i, v in enumerate(grid["vals"]):
        if v != v:
            out[i] = k
        else:
            q = int((v - lo) / span * k)
            out[i] = 0 if q < 0 else (km1 if q > km1 else q)
    b = bytes(out)
    with _lock:
        _soil_q_cache[key] = b
        while len(_soil_q_cache) > 48:
            _soil_q_cache.pop(next(iter(_soil_q_cache)))
    return b


def _png_soil(w, h, pix, palette, ncol):
    alpha = bytes(255 if v < ncol else 0 for v in range(256))
    raw = b"".join(b"\x00" + pix[y * w:(y + 1) * w] for y in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 3, 0, 0, 0))
            + _png_chunk(b"PLTE", palette)
            + _png_chunk(b"tRNS", alpha)
            + _png_chunk(b"IDAT", zlib.compress(raw, 6))
            + _png_chunk(b"IEND", b""))


_soil_png_cache = {}


def soil_png(day, area_id, bbox, width):
    """Soil raster warped into a plain lon/lat rectangle as a transparent PNG."""
    g = load_soil_grid(day, area_id)
    if not g:
        return None
    dom = soil_domain()
    pkey = (day, area_id, g["mtime"], bbox, width, round(dom[0], 6), round(dom[1], 6))
    with _lock:
        hit = _soil_png_cache.get(pkey)
    if hit:
        return hit
    w, h, geo = g["w"], g["h"], g["geo"]
    lon_a, lon_b, lat_a, lat_b = bbox
    W = width
    H = max(1, min(2400, round(
        W * (lat_b - lat_a) / ((lon_b - lon_a) * math.cos((lat_a + lat_b) / 2 * _D2R)))))
    ikey = ("soil", w, h, geo, bbox, W, H)

    def to_src(px, py):
        return laea_grid(lon_a + (px + 0.5) * (lon_b - lon_a) / W,
                         lat_b - (py + 0.5) * (lat_b - lat_a) / H, geo)

    with _lock:
        idx = _idx_cache.get(ikey)
    if idx is None:
        idx = _warp_idx(W, H, to_src, w, h)
        with _lock:
            _idx_cache[ikey] = idx
    lut = soil_quantize(day, area_id, g, dom, _SOIL_K) + bytes([_SOIL_K])  # idx -1 -> nodata
    pix = bytes(map(lut.__getitem__, idx))
    png = _png_soil(W, H, pix, soil_palette(_SOIL_K), _SOIL_K)
    with _lock:
        _soil_png_cache[pkey] = png
        while len(_soil_png_cache) > 64:
            _soil_png_cache.pop(next(iter(_soil_png_cache)))
    return png


def build_soil_info(dates):
    areas = load_soil_areas()
    days = list_soil_days()
    if not areas or not days:
        return None
    daymap = {}
    for d in days:
        t = soil_day_token(d, areas)
        if t is not None:
            daymap[d] = t
    if not daymap:
        return None
    dom = soil_domain()
    series = {}
    for a in areas:
        row = []
        for iso in dates:
            d = iso.replace("-", "")
            row.append(soil_area_mean(d, a["area_id"])
                       if d in daymap and soil_mtime(d, a["area_id"]) else None)
        series[str(a["area_id"])] = row
    return {
        "product": "Soil moisture",
        "areas": [_soil_area_pub(a) for a in areas],
        "days": daymap,
        "series": series,
        "domain": [round(dom[0], 4), round(dom[1], 4)],
        "units": soil_units(dom),
        "colormap": soil_colormap(_SOIL_K),
    }


# ------------------------------------------------------ RawIF pass track
# planner/output/YYYYMMDD/CYG<norad>_plan.csv lists the seconds-of-day (UTC)
# each CYGNSS satellite is commanded into RawIF capture; orbits-actual/
# specular_trajectory_YYYY-MM-DD.csv carries the day's *actual* 2 Hz
# specular-point locations (4 channels per satellite). A day's "RawIF track"
# is every channel's actual specular point at each commanded second — all 4
# channels, deliberately unfiltered (user decision 2026-07-15): a commanded
# second captures the whole receiver, so channels that land outside CONUS
# (Gulf, Mexico, ocean) are shown as flown. First 2 Hz sample per
# satellite-second (adjacent samples are ~3 km apart — sub-pixel on the
# CONUS map). All points fall within lat 17–42, lon −133…−64, so the map
# projection needs no guard. Extraction streams the ~170 MB trajectory CSV
# once (~1.5 s); results are cached gzipped by file mtimes.
#
# The plans also carry "DNL: <station>" rows — one per downlink second to a
# ground station (AUS / HI / CHI). The payload's "storage" block simulates
# each satellite's onboard buffer through the day (starts empty at 00:00
# UTC): +100/60 % per RawIF observation (60-image buffer), −100/1200 % per
# downlink second (20 min drains a full buffer), clamped to [0, 100]. It is
# served as per-sat piecewise-linear breakpoints plus the downlink windows,
# so the console can animate storage bars and light the station boxes on
# the same sweep clock.

_PLAN_RE = re.compile(r"^CYG(\d+)_plan\.csv$")
_RAWIF_VER = "fleet-v1"      # salt: bump when the extraction logic changes
_GS_ORDER = ("AUS", "HI", "CHI")
_OBS_PCT = 100.0 / 60.0      # storage % filled per observation
_DNL_PCT = 100.0 / 1200.0    # storage % freed per downlink second


def orbit_csv_path(day):
    return os.path.join(ORBIT_ROOT,
                        f"specular_trajectory_{day[0:4]}-{day[4:6]}-{day[6:8]}.csv")


def _rawif_fp(day):
    """Fingerprint of everything a day's track derives from, or None if the
    day is missing either its plan CSVs or its trajectory CSV."""
    try:
        st = os.stat(orbit_csv_path(day))
        parts = [(_RAWIF_VER,), ("traj", int(st.st_mtime), st.st_size)]
        plan_dir = os.path.join(PLAN_ROOT, day)
        for name in sorted(os.listdir(plan_dir)):
            if _PLAN_RE.match(name):
                ps = os.stat(os.path.join(plan_dir, name))
                parts.append((name, int(ps.st_mtime), ps.st_size))
    except OSError:
        return None
    return tuple(parts) if len(parts) > 2 else None   # salt + traj + ≥1 plan


def list_rawif_days():
    try:
        entries = sorted(e for e in os.listdir(PLAN_ROOT) if DAY_RE.match(e))
    except OSError:
        return []
    return [d for d in entries if _rawif_fp(d) is not None]


def _rawif_token(fp):
    return hashlib.md5(repr(fp).encode()).hexdigest()[:10]


_rawif_cache = {}   # day -> (fp, gzipped payload bytes)


def _load_plan_events(plan_dir):
    """norad str -> {"obs": set(sec), "dnl": {sec: station}}, per CYG*_plan.csv."""
    plans = {}
    for name in sorted(os.listdir(plan_dir)):
        m = _PLAN_RE.match(name)
        if not m:
            continue
        obs, dnl = set(), {}
        with open(os.path.join(plan_dir, name)) as fh:
            for line in fh:
                parts = line.split(",")
                if len(parts) != 2:
                    continue
                try:
                    sec = int(parts[0])
                except ValueError:
                    continue
                cmd = parts[1].strip()
                if cmd == "RawIF":
                    obs.add(sec)
                elif cmd.startswith("DNL:"):
                    dnl[sec] = cmd[4:].strip()
        if obs or dnl:
            plans[m.group(1)] = {"obs": obs, "dnl": dnl}
    return plans


def _storage_breakpoints(obs, dnl):
    """Piecewise-linear storage-% breakpoints (t, v) over one day: an
    observation steps +_OBS_PCT at its second, each downlink second drains
    _DNL_PCT over that second, level clamped to [0, 100]. Collinear runs
    (steady drains, per-second obs bursts) collapse to their endpoints."""
    ts, vs = [0], [0.0]

    def emit(t, v):
        if len(ts) >= 2:
            prev = (vs[-1] - vs[-2]) / (ts[-1] - ts[-2])
            if abs((v - vs[-1]) / (t - ts[-1]) - prev) < 1e-9:
                ts[-1] = t
                vs[-1] = v
                return
        ts.append(t)
        vs.append(v)

    lv = 0.0
    for s in sorted(obs | set(dnl)):
        if ts[-1] < s:
            emit(s, lv)              # hold flat through the idle stretch
        if s in obs:
            lv = min(100.0, lv + _OBS_PCT)
        if s in dnl:
            lv = max(0.0, lv - _DNL_PCT)
        emit(s + 1, lv)
    if ts[-1] < 86400:
        emit(86400, lv)
    return ts, [round(v, 2) for v in vs]


def _dnl_windows(dnl):
    """Contiguous same-station downlink runs as [start, end) windows."""
    wins = []
    for s in sorted(dnl):
        if wins and s == wins[-1][1] and dnl[s] == wins[-1][2]:
            wins[-1][1] = s + 1
        else:
            wins.append([s, s + 1, dnl[s]])
    return wins


def load_rawif(day):
    """Gzipped JSON track for one day, cached by plan+trajectory fingerprint."""
    fp = _rawif_fp(day)
    if fp is None:
        return None
    with _lock:
        hit = _rawif_cache.get(day)
        if hit and hit[0] == fp:
            return hit[1]
    plan_dir = os.path.join(PLAN_ROOT, day)
    try:
        plans = _load_plan_events(plan_dir)
        sats = sorted(plans)            # NORAD ids, index = per-point sat tag
        sidx = {norad: i for i, norad in enumerate(sats)}
        pts = []
        seen = set()                    # (norad, sec) -> keep first 2 Hz sample
        with open(orbit_csv_path(day)) as fh:
            fh.readline()               # header
            for line in fh:
                c = line.split(",")
                plan = plans.get(c[1])
                if plan is None:
                    continue
                f = c[2]                # "YYYY-MM-DD HH:MM:SS.frac" (fixed layout)
                sec = int(f[11:13]) * 3600 + int(f[14:16]) * 60 + int(f[17:19])
                if sec not in plan["obs"] or (c[1], sec) in seen:
                    continue
                seen.add((c[1], sec))
                for k in (3, 6, 9, 12):
                    try:
                        la = float(c[k])
                        lo = float(c[k + 1])
                    except (ValueError, IndexError):
                        continue
                    pts.append((sec, round(la, 2), round(lo, 2), sidx[c[1]]))
    except OSError as e:
        print(f"[rawif] {day}: cannot build track: {e}", file=sys.stderr)
        return None
    pts.sort()
    seen_gs = {st for p in plans.values() for st in p["dnl"].values()}
    stations = [g for g in _GS_ORDER if g in seen_gs] + sorted(seen_gs - set(_GS_ORDER))
    gidx = {g: i for i, g in enumerate(stations)}
    lv, dnl_out = [], []
    for i, norad in enumerate(sats):
        p = plans[norad]
        bt, bv = _storage_breakpoints(p["obs"], p["dnl"])
        lv.append({"t": bt, "v": bv})
        for w in _dnl_windows(p["dnl"]):
            dnl_out.append([w[0], w[1], i, gidx[w[2]]])
    dnl_out.sort()
    payload = {"day": day, "n": len(pts), "n_sats": len(plans), "sats": sats,
               "t": [p[0] for p in pts],
               "lat": [p[1] for p in pts], "lon": [p[2] for p in pts],
               "s": [p[3] for p in pts],
               "storage": {"limit": 60, "obs_pct": round(_OBS_PCT, 4),
                           "dnl_pct": round(_DNL_PCT, 4),
                           "stations": stations, "lv": lv, "dnl": dnl_out}}
    co = zlib.compressobj(6, zlib.DEFLATED, 31)   # gzip container
    gz = co.compress(json.dumps(payload, separators=(",", ":")).encode()) + co.flush()
    with _lock:
        _rawif_cache[day] = (fp, gz)
    return gz


def rawif_bundle_info():
    days = {}
    for day in list_rawif_days():
        fp = _rawif_fp(day)
        if fp is not None:
            days[day] = _rawif_token(fp)
    if not days:
        return None
    return {"product": "RawIF pass track", "days": days}


def list_days():
    try:
        entries = sorted(e for e in os.listdir(CONFIG_ROOT) if DAY_RE.match(e))
    except OSError:
        return []
    return [d for d in entries if os.path.isfile(os.path.join(CONFIG_ROOT, d, "fires.json"))]


def data_version():
    """Cheap fingerprint of everything the bundle depends on."""
    h = hashlib.md5()
    for day in list_days():
        cfg = os.path.join(CONFIG_ROOT, day, "fires.json")
        h.update(f"{day}:{os.path.getmtime(cfg)}".encode())
        dm = danger_mtime(day)
        if dm is not None:
            h.update(f"danger:{day}:{dm}".encode())
        det_dir = os.path.join(DETECT_ROOT, day)
        if os.path.isdir(det_dir):
            for name in sorted(os.listdir(det_dir)):
                if name.startswith("burned_") and name.endswith(".csv"):
                    p = os.path.join(det_dir, name)
                    try:
                        st = os.stat(p)
                        h.update(f"{day}/{name}:{st.st_mtime}:{st.st_size}".encode())
                    except OSError:
                        pass
    soil_areas = load_soil_areas()
    for day in list_soil_days():
        for a in soil_areas:
            m = soil_mtime(day, a["area_id"])
            if m is not None:
                h.update(f"soil:{day}:{a['area_id']}:{m}".encode())
    for day in list_rawif_days():
        h.update(f"rawif:{day}:{_rawif_fp(day)}".encode())
    return h.hexdigest()[:12]


def build_bundle():
    """Full data model: watchlist per day + unique fires + detection summaries."""
    days_ids = list_days()
    dates, day_list = [], []
    fire_days, fire_meta = {}, {}

    for di, day in enumerate(days_ids):
        date_iso = f"{day[0:4]}-{day[4:6]}-{day[6:8]}"
        dates.append(date_iso)
        with open(os.path.join(CONFIG_ROOT, day, "fires.json")) as fh:
            config = json.load(fh)
        recs = []
        for f in config:
            clat = (f["lat_upper"] + f["lat_lower"]) / 2
            clon = (f["lon_upper"] + f["lon_lower"]) / 2
            reg = region_of(clat, clon)
            fid = f["irwin_id"]
            det = load_detections(day, f["name"])
            recs.append({
                "id": fid, "name": f["name"],
                "lat_u": round(f["lat_upper"], 4), "lat_l": round(f["lat_lower"], 4),
                "lon_u": round(f["lon_upper"], 4), "lon_l": round(f["lon_lower"], 4),
                "clat": round(clat, 4), "clon": round(clon, 4),
                "region": reg, "fire_start": f["fire_start_date"],
                "n_obs": det["n"] if det else None,
                "n_burned": det["n_burned"] if det else None,
            })
            fire_days.setdefault(fid, []).append(di)
            fire_meta.setdefault(fid, recs[-1])
        day_list.append({"day": day, "date": date_iso, "fires": recs})

    fires = []
    for fid, dl in fire_days.items():
        m = fire_meta[fid]
        series = [None] * len(dates)
        for day_entry in day_list:
            for r in day_entry["fires"]:
                if r["id"] == fid and r["n_obs"] is not None:
                    series[dates.index(day_entry["date"])] = [r["n_obs"], r["n_burned"]]
        fires.append({
            "id": fid, "name": m["name"], "region": m["region"],
            "region_name": REGION_NAMES[m["region"]],
            "clat": m["clat"], "clon": m["clon"],
            "lat_u": m["lat_u"], "lat_l": m["lat_l"],
            "lon_u": m["lon_u"], "lon_l": m["lon_l"],
            "fire_start": m["fire_start"],
            "first_day": min(dl), "last_day": max(dl),
            "day_indices": dl, "days_count": len(dl),
            "series": series,
        })
    fires.sort(key=lambda x: (x["first_day"], -x["days_count"]))

    danger = danger_bundle_info(days_ids)
    if danger:
        for f in fires:
            f["danger"] = [
                danger_box_mean(days_ids[i], f["lat_l"], f["lat_u"], f["lon_l"], f["lon_u"])
                if days_ids[i] in danger["days"] else None
                for i in range(len(days_ids))
            ]

    soil = build_soil_info(dates)

    return {
        "version": data_version(),
        "dates": dates,
        "days": day_list,
        "fires": fires,
        "danger": danger,
        "soil": soil,
        "rawif": rawif_bundle_info(),
        "meta": {
            "n_days": len(days_ids),
            "n_unique": len(fires),
            "slots_per_day": max((len(d["fires"]) for d in day_list), default=0),
            "campaign_start": dates[0] if dates else None,
            "campaign_end": dates[-1] if dates else None,
            "regions": REGION_NAMES,
        },
    }


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "FireConsole/1.0"

    def _send(self, code, body, ctype="application/json", cache="no-store", enc=None):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if enc:
            self.send_header("Content-Encoding", enc)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self, qs):
        if not TOKEN:
            return True
        if qs.get("token", [""])[0] == TOKEN:
            return True
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {TOKEN}"

    def do_GET(self):
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        if not self._authorized(qs):
            return self._send(401, {"error": "missing or bad token"})

        if url.path in STATIC_FILES:
            fname, ctype = STATIC_FILES[url.path]
            try:
                with open(os.path.join(BASE, "static", fname), "rb") as fh:
                    return self._send(200, fh.read(), ctype)
            except OSError:
                return self._send(404, {"error": "static file missing"})

        if url.path == "/api/version":
            return self._send(200, {"version": data_version()})

        if url.path == "/api/bundle":
            return self._send(200, build_bundle())

        if url.path == "/api/danger":
            day = qs.get("day", [""])[0]
            view = qs.get("view", ["conus"])[0]
            if not DAY_RE.match(day):
                return self._send(400, {"error": "bad day parameter"})
            if view == "conus":
                png = danger_png(day, "conus")
            elif view == "geo":
                try:
                    bbox = tuple(float(x) for x in qs.get("bbox", [""])[0].split(","))
                    wpx = int(qs.get("w", ["600"])[0])
                    ok = (len(bbox) == 4 and bbox[0] < bbox[1] and bbox[2] < bbox[3]
                          and bbox[1] - bbox[0] <= 30 and bbox[3] - bbox[2] <= 20
                          and -180 <= bbox[0] and bbox[1] <= 180
                          and -80 <= bbox[2] and bbox[3] <= 80 and 64 <= wpx <= 1600)
                except ValueError:
                    ok = False
                if not ok:
                    return self._send(400, {"error": "bad bbox or w parameter"})
                png = danger_png(day, "geo", bbox, wpx)
            else:
                return self._send(400, {"error": "bad view parameter"})
            if png is None:
                return self._send(404, {"error": f"no WFPI Day-1 product for {day}"})
            return self._send(200, png, "image/png", cache="public, max-age=86400")

        if url.path == "/api/soil":
            day = qs.get("day", [""])[0]
            try:
                area_id = int(qs.get("area", [""])[0])
            except ValueError:
                area_id = -1
            if not DAY_RE.match(day) or area_id < 0:
                return self._send(400, {"error": "bad day or area parameter"})
            try:
                bbox = tuple(float(x) for x in qs.get("bbox", [""])[0].split(","))
                wpx = int(qs.get("w", ["320"])[0])
                ok = (len(bbox) == 4 and bbox[0] < bbox[1] and bbox[2] < bbox[3]
                      and bbox[1] - bbox[0] <= 30 and bbox[3] - bbox[2] <= 20
                      and -180 <= bbox[0] and bbox[1] <= 180
                      and -80 <= bbox[2] and bbox[3] <= 80 and 32 <= wpx <= 1200)
            except ValueError:
                ok = False
            if not ok:
                return self._send(400, {"error": "bad bbox or w parameter"})
            png = soil_png(day, area_id, bbox, wpx)
            if png is None:
                return self._send(404, {"error": f"no soil raster for area {area_id} on {day}"})
            return self._send(200, png, "image/png", cache="public, max-age=86400")

        if url.path == "/api/rawif":
            day = qs.get("day", [""])[0]
            if not DAY_RE.match(day):
                return self._send(400, {"error": "bad day parameter"})
            gz = load_rawif(day)
            if gz is None:
                return self._send(404, {"error": f"no RawIF plan/trajectory for {day}"})
            if "gzip" in self.headers.get("Accept-Encoding", ""):
                return self._send(200, gz, cache="public, max-age=86400", enc="gzip")
            return self._send(200, zlib.decompress(gz, 47), cache="public, max-age=86400")

        if url.path == "/api/detections":
            day = qs.get("day", [""])[0]
            fire = qs.get("fire", [""])[0]
            if not DAY_RE.match(day) or not re.match(r"^[A-Za-z0-9_\- ]+$", fire):
                return self._send(400, {"error": "bad day or fire parameter"})
            det = load_detections(day, fire)
            if det is None:
                return self._send(404, {"error": f"no detections for {fire} on {day}"})
            return self._send(200, det)

        return self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


# Same bboxes the console's insets request, so their PNGs are pre-warmed too.
WARM_INSET_BBOXES = ((-112.55, -108.05, 32.5, 35.6), (-82.05, -80.0, 24.9, 29.6))


def soil_warm_bbox(a):
    """Display bbox for a soil area — must match soilBbox() in static/index.html."""
    pl = round((a["lon_u"] - a["lon_l"]) * 0.08, 3)
    pt = round((a["lat_u"] - a["lat_l"]) * 0.08, 3)
    return (round(a["lon_l"] - pl, 3), round(a["lon_u"] + pl, 3),
            round(a["lat_l"] - pt, 3), round(a["lat_u"] + pt, 3))


def warmup():
    try:
        b = build_bundle()
        total = sum(r["n_obs"] or 0 for d in b["days"] for r in d["fires"])
        print(f"[warmup] cached {b['meta']['n_days']} days, "
              f"{b['meta']['n_unique']} fires, {total} detections", flush=True)
        rendered = 0
        if b.get("danger"):
            for day in sorted(b["danger"]["days"]):
                if danger_png(day, "conus"):
                    rendered += 1
                for bbox in WARM_INSET_BBOXES:
                    danger_png(day, "geo", bbox, 600)
        print(f"[warmup] rendered WFPI Day-1 rasters for {rendered} days", flush=True)
        if b.get("soil"):
            srendered = 0
            for day in sorted(b["soil"]["days"]):
                for a in b["soil"]["areas"]:
                    if soil_png(day, a["id"], soil_warm_bbox(a), 320):
                        srendered += 1
            print(f"[warmup] rendered {srendered} soil rasters "
                  f"({len(b['soil']['areas'])} sites)", flush=True)
        if b.get("rawif"):
            built = sum(1 for day in sorted(b["rawif"]["days"]) if load_rawif(day))
            print(f"[warmup] built RawIF tracks for {built} days", flush=True)
    except Exception as e:  # noqa: BLE001 — warmup is best-effort
        print(f"[warmup] failed: {e}", flush=True)


if __name__ == "__main__":
    print(f"config root: {CONFIG_ROOT}")
    print(f"detect root: {DETECT_ROOT}")
    print(f"auth token : {'ON' if TOKEN else 'off (localhost use)'}")
    threading.Thread(target=warmup, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"serving on http://localhost:{PORT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
