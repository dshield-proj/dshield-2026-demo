#!/usr/bin/env python3
"""Render a predicted-vs-actual specular-point animation for one day, for PowerPoint.

The view is a lat/lon bounded region — by default a padded box around the
**Pocket** fire (from dshield-demo-configuration/burned-area-config/<day>/
fires.json); pick another watchlist fire with --fire or give explicit bounds
with --bbox LATMIN LATMAX LONMIN LONMAX.

For every planner-commanded RawIF second (planner/output/<day>/CYG<norad>_plan.csv)
the animation shows, inside the region:
  - every predicted specular point (orbits/output/<day>/CYG<norad>/specular/
    specular.csv — the planning-time orbit prediction, all visible GPS
    transmitters) as a filled blue dot,
  - every actually recorded specular point (orbits-actual/
    specular_trajectory_YYYY-MM-DD.csv, first 2 Hz sample per second, top-4
    channels by signal strength) as a filled green dot,
  - a connecting line between each matched pair (same satellite + second +
    GPS transmitter; if one end of a pair falls just outside the region both
    ends are still drawn, so connectors never dangle).
Fire watchlist boxes, a graticule and a km scale bar give context; side panels
show the actual-minus-predicted offset "dartboard" in km and offset vs UTC
time. The sweep runs pass by pass with idle gaps skipped, paced so sparse
regional crossings stay watchable (--rate = data-seconds per video second).

Offsets are only ~1-4 km, sub-pixel at fire-region scale, so by default each
matched green dot sits exactly on its blue partner and the connector is
invisible (the dartboard panel carries the error story at true scale). Use
--exaggerate N to stretch each actual dot N x its true offset away from its
predicted partner on the map (annotated on the figure; the dartboard and the
time strip always stay true-scale), or a tight --bbox a few km across.

Output is an H.264 MP4 (PowerPoint: Insert -> Video -> This Device; tick
"Loop until Stopped" for a demo loop) or an animated GIF with --gif
(Insert -> Pictures; auto-plays in slideshow). A poster PNG of the final
frame is written next to the video.

Usage:
  python3 helper_scripts/rawif_pred_vs_actual_anim.py 20260702
  python3 helper_scripts/rawif_pred_vs_actual_anim.py 20260702 --fire Sycamore
  python3 helper_scripts/rawif_pred_vs_actual_anim.py 20260702 --bbox 28.5 29.6 -82.0 -80.8
  python3 helper_scripts/rawif_pred_vs_actual_anim.py 20260702 --map      # static full-day map PNG
  python3 helper_scripts/rawif_pred_vs_actual_anim.py 20260702 --preview  # 3 stills, no video

Needs numpy + matplotlib (+ imageio-ffmpeg for MP4); the repo data needs a day
present in all three of planner/output, orbits/output and orbits-actual
(20260630..20260711).
"""

import argparse
import glob
import json
import math
import os
import sys

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FFMpegWriter, FuncAnimation, PillowWriter
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from matplotlib.patches import Circle

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Light-mode palette (dataviz reference palette; slides are light surfaces).
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
LAND = "#f3f2ee"
PRED = "#2a78d6"  # categorical slot 1 (blue)  — predicted dot
ACT = "#008300"  # categorical slot 2 (green) — actual dot

R_EARTH = 6371.0


# ---------------------------------------------------------------- data loading

def load_plans(day):
    """{norad: RawIF seconds} from planner/output/<day>/CYG*_plan.csv."""
    plans = {}
    for path in sorted(glob.glob(f"{BASE}/planner/output/{day}/CYG*_plan.csv")):
        norad = int(os.path.basename(path)[3:8])
        secs = set()
        with open(path) as f:
            for line in f:
                if "RawIF" in line:
                    secs.add(int(line.split(",")[0]))
        if secs:
            plans[norad] = secs
    return plans


def load_predicted(day, plans):
    """{(norad, sec): {gps_norad: (lat, lon)}} at commanded seconds only.

    specular.csv rows are grouped by transmitter, not time-sorted, so each file
    is scanned in full. Longitudes arrive 0..360 and are normalised to +/-180.
    """
    pred = {}
    for norad, secs in plans.items():
        path = f"{BASE}/orbits/output/{day}/CYG{norad}/specular/specular.csv"
        with open(path) as f:
            for _ in range(5):
                f.readline()
            for line in f:
                parts = line.split(",")
                try:
                    t = int(parts[0])
                except ValueError:
                    continue
                if t in secs:
                    lon = (float(parts[3]) + 180.0) % 360.0 - 180.0
                    gps = int(parts[1][4:])  # "GNSS<norad>"
                    pred.setdefault((norad, t), {})[gps] = (float(parts[2]), lon)
    return pred


def load_actual(day, plans):
    """{(norad, sec): [(gps_norad, lat, lon), ...]} — first 2 Hz sample of each
    commanded second, all 4 channels (channels with empty fields dropped)."""
    dash = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    path = f"{BASE}/orbits-actual/specular_trajectory_{dash}.csv"
    act = {}
    with open(path) as f:
        f.readline()
        for line in f:
            p = line.rstrip("\n").split(",")
            norad = int(p[1])
            if norad not in plans:
                continue
            t = p[2]
            sec = int(t[11:13]) * 3600 + int(t[14:16]) * 60 + int(t[17:19])
            if sec not in plans[norad] or (norad, sec) in act:
                continue
            chans = []
            for i in range(4):
                g, la, lo = p[5 + 3 * i].strip(), p[3 + 3 * i].strip(), p[4 + 3 * i].strip()
                if g and la and lo:
                    chans.append((int(g), float(la), float(lo)))
            act[(norad, sec)] = chans
    return act


def load_day_fires(day):
    """[(name, lat_lower, lat_upper, lon_lower, lon_upper), ...] for the day."""
    path = f"{BASE}/dshield-demo-configuration/burned-area-config/{day}/fires.json"
    if not os.path.exists(path):
        return []
    return [(f["name"], f["lat_lower"], f["lat_upper"], f["lon_lower"], f["lon_upper"])
            for f in json.load(open(path))]


def fire_box(day, name):
    """Watchlist box for a fire, searching the day's config first, then all days."""
    cfg = f"{BASE}/dshield-demo-configuration/burned-area-config"
    days = [day] + sorted(d for d in os.listdir(cfg) if d != day)
    for d in days:
        for n, la0, la1, lo0, lo1 in load_day_fires(d):
            if n.lower() == name.lower():
                return la0, la1, lo0, lo1
    sys.exit(f"fire '{name}' not found in {cfg}/*/fires.json")


def build_dataset(pred, act, bbox):
    """Flatten to region-filtered numpy arrays.

    Predicted points keep every visible transmitter at each commanded second;
    actual points are the recorded top-4 channels; links pair them by
    (satellite, second, GPS transmitter). A point outside the region is kept
    when its pair partner is inside.
    """
    la0, la1, lo0, lo1 = bbox
    prows, pidx = [], {}
    for (sat, sec), by_gps in pred.items():
        for gps, (la, lo) in by_gps.items():
            pidx[(sat, sec, gps)] = len(prows)
            prows.append((sec, sat, la, lo))
    arows, links = [], []
    for (sat, sec), chans in act.items():
        for gps, la, lo in chans:
            ip = pidx.get((sat, sec, gps))
            if ip is not None:
                links.append((ip, len(arows)))
            arows.append((sec, sat, la, lo))
    P = np.array(prows, float).reshape(-1, 4)
    A = np.array(arows, float).reshape(-1, 4)
    L = np.array(links, int).reshape(-1, 2)

    def inbox(M):
        return (M[:, 2] >= la0) & (M[:, 2] <= la1) & (M[:, 3] >= lo0) & (M[:, 3] <= lo1)

    kp, ka = inbox(P), inbox(A)
    kl = (kp[L[:, 0]] | ka[L[:, 1]]) if len(L) else np.zeros(0, bool)
    kp2, ka2 = kp.copy(), ka.copy()
    if len(L):
        kp2[L[kl, 0]] = True
        ka2[L[kl, 1]] = True
    pmap, amap = np.cumsum(kp2) - 1, np.cumsum(ka2) - 1
    P, A = P[kp2], A[ka2]
    L = (np.column_stack((pmap[L[kl, 0]], amap[L[kl, 1]]))
         if kl.any() else np.zeros((0, 2), int))

    d = {
        "psec": P[:, 0], "psat": P[:, 1].astype(int), "plat": P[:, 2], "plon": P[:, 3],
        "asec": A[:, 0], "asat": A[:, 1].astype(int), "alat": A[:, 2], "alon": A[:, 3],
        "lp": L[:, 0], "la": L[:, 1],
    }
    if len(L):
        plat, plon = d["plat"][L[:, 0]], d["plon"][L[:, 0]]
        alat, alon = d["alat"][L[:, 1]], d["alon"][L[:, 1]]
        mlat = np.radians((alat + plat) / 2.0)
        d["de"] = (alon - plon) * 111.320 * np.cos(mlat)
        d["dn"] = (alat - plat) * 110.574
        d["lsec"] = d["asec"][L[:, 1]]
    else:
        d["de"] = d["dn"] = d["lsec"] = np.zeros(0)
    d["dkm"] = np.hypot(d["de"], d["dn"])
    return d


# ------------------------------------------------------------------ projection

def albers(lat, lon, sp1=29.5, sp2=45.5, lat0=23.0, lon0=-96.0):
    """Spherical Albers equal-area conic (km)."""
    lat, lon = np.radians(np.asarray(lat, float)), np.radians(np.asarray(lon, float))
    p1, p2, p0, l0 = map(math.radians, (sp1, sp2, lat0, lon0))
    n = (math.sin(p1) + math.sin(p2)) / 2.0
    c = math.cos(p1) ** 2 + 2.0 * n * math.sin(p1)
    rho = R_EARTH * np.sqrt(c - 2.0 * n * np.sin(lat)) / n
    rho0 = R_EARTH * math.sqrt(c - 2.0 * n * math.sin(p0)) / n
    th = n * (lon - l0)
    return rho * np.sin(th), rho0 - rho * np.cos(th)


def state_outlines():
    """Projected rings from the fire-console CONUS geojson."""
    gj = json.load(open(f"{BASE}/fire-console/static/conus.json"))
    rings = []
    for ft in gj["features"]:
        geom = ft["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            ring = np.array(poly[0], float)
            x, y = albers(ring[:, 1], ring[:, 0])
            rings.append((x, y))
    return rings


def grat_step(span_deg):
    for s in (0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10):
        if span_deg / s <= 7:
            return s
    return 20


def region_extent(region):
    """Projected centre and padded spans (km) of the region's bounding box."""
    la0, la1, lo0, lo1 = region["bbox"]
    ns = 60
    ed = np.linspace
    bx, by = albers(
        np.concatenate((np.full(ns, la0), np.full(ns, la1), ed(la0, la1, ns), ed(la0, la1, ns))),
        np.concatenate((ed(lo0, lo1, ns), ed(lo0, lo1, ns), np.full(ns, lo0), np.full(ns, lo1))))
    xspan = (bx.max() - bx.min()) * 1.04
    yspan = (by.max() - by.min()) * 1.04
    return (bx.max() + bx.min()) / 2, (by.max() + by.min()) / 2, xspan, yspan


def draw_map_chrome(axm, region, fs=1.0):
    """States, graticule, fire watchlist boxes and scale bar on a prepared map
    axes (limits already set). fs scales fonts and linework."""
    la0, la1, lo0, lo1 = region["bbox"]
    ed = np.linspace
    for x, y in state_outlines():
        axm.fill(x, y, facecolor=LAND, edgecolor=BASELINE, linewidth=0.8 * fs, zorder=1)

    gs = grat_step(max(la1 - la0, lo1 - lo0))
    for la in np.arange(math.ceil(la0 / gs) * gs, la1 + 1e-9, gs):
        gx, gy = albers(np.full(80, la), ed(lo0, lo1, 80))
        axm.plot(gx, gy, color=GRID, lw=0.6 * fs, zorder=1.4)
        axm.annotate(f"{la:g}°", (gx[2], gy[2]), textcoords="offset points",
                     xytext=(2, 2), color=MUTED, fontsize=7.5 * fs, zorder=5)
    for lo in np.arange(math.ceil(lo0 / gs) * gs, lo1 + 1e-9, gs):
        gx, gy = albers(ed(la0, la1, 80), np.full(80, lo))
        axm.plot(gx, gy, color=GRID, lw=0.6 * fs, zorder=1.4)
        axm.annotate(f"{lo:g}°", (gx[2], gy[2]), textcoords="offset points",
                     xytext=(3, 1), color=MUTED, fontsize=7.5 * fs, zorder=5)

    for name, fla0, fla1, flo0, flo1 in region["fires"]:
        if fla1 < la0 or fla0 > la1 or flo1 < lo0 or flo0 > lo1:
            continue
        ring_la = np.concatenate((np.full(30, fla0), ed(fla0, fla1, 30),
                                  np.full(30, fla1), ed(fla1, fla0, 30)))
        ring_lo = np.concatenate((ed(flo0, flo1, 30), np.full(30, flo1),
                                  ed(flo1, flo0, 30), np.full(30, flo0)))
        fx, fy = albers(ring_la, ring_lo)
        axm.plot(fx, fy, color=MUTED, lw=1.0 * fs, ls=(0, (5, 3)), zorder=2.2)
        tx, ty = albers(fla1, flo0)
        axm.annotate(f" {name}", (float(tx), float(ty)), textcoords="offset points",
                     xytext=(2, 4), color=INK_2, fontsize=9 * fs, zorder=5)

    xmin, xmax = axm.get_xlim()
    ymin, ymax = axm.get_ylim()
    wkm = xmax - xmin
    sb = next(s for s in (500, 200, 100, 50, 20, 10, 5, 2, 1) if s <= wkm * 0.3)
    x0s, y0s = xmin + wkm * 0.05, ymin + (ymax - ymin) * 0.055
    tick = (ymax - ymin) * 0.012
    axm.plot([x0s, x0s + sb], [y0s, y0s], color=INK_2, lw=1.5 * fs,
             solid_capstyle="butt", zorder=6)
    for xc in (x0s, x0s + sb):
        axm.plot([xc, xc], [y0s - tick, y0s + tick], color=INK_2, lw=1.2 * fs, zorder=6)
    axm.annotate(f"{sb} km", (x0s + sb / 2.0, y0s), textcoords="offset points",
                 xytext=(0, 4 * fs), ha="center", color=INK_2, fontsize=8.5 * fs, zorder=6)


# ------------------------------------------------------------------- animation

def make_animation(day, D, region, args):
    la0, la1, lo0, lo1 = region["bbox"]
    psec, asec, lsec, dkm = D["psec"], D["asec"], D["lsec"], D["dkm"]
    ppts = np.column_stack(albers(D["plat"], D["plon"])) if len(psec) else np.zeros((0, 2))
    apts = np.column_stack(albers(D["alat"], D["alon"])) if len(asec) else np.zeros((0, 2))
    if args.exaggerate != 1.0 and len(D["lp"]):
        # stretch each matched actual dot away from its predicted partner;
        # unmatched actual dots (no partner) stay at their true position
        apts[D["la"]] = ppts[D["lp"]] + args.exaggerate * (apts[D["la"]] - ppts[D["lp"]])
    segs = (np.stack((ppts[D["lp"]], apts[D["la"]]), axis=1)
            if len(D["lp"]) else np.zeros((0, 2, 2)))
    dart = np.column_stack((D["de"], D["dn"])) if len(dkm) else np.zeros((0, 2))
    lhours = lsec / 3600.0

    uniq_full = np.unique(np.concatenate((psec, asec)))
    n_sec_region = len(uniq_full)
    uniq = list(uniq_full[:: args.stride])
    if uniq[-1] != uniq_full[-1]:
        uniq.append(uniq_full[-1])
    rate = args.rate or min(args.fps, max(2.0, len(uniq) / 20.0))
    n_rep = max(1, int(round(args.fps / rate)))
    times, bases = [], []
    for s in uniq:
        for k in range(n_rep):
            times.append(s + k / n_rep)
            bases.append(s)
    n_hold = int(round(args.fps * 2.5))
    n_frames = len(times) + n_hold

    # pass segmentation (gap > 300 s) for the HUD
    starts = [0] + [i for i in range(1, len(uniq)) if uniq[i] - uniq[i - 1] > 300]
    pass_idx = {}
    for i, s in enumerate(uniq):
        pass_idx[s] = sum(1 for st in starts if st <= i)
    n_pass = len(starts)

    fig = plt.figure(figsize=(12.8, 7.2), dpi=args.dpi)
    fig.patch.set_facecolor(SURFACE)
    dash = f"{day[:4]}-{day[4:6]}-{day[6:]}"

    fig.text(0.045, 0.945, "CYGNSS RawIF tasking — predicted vs actual specular points",
             color=INK, fontsize=15, fontweight="bold")
    fig.text(0.045, 0.905,
             f"{dash} · {region['label']} · {n_sec_region} commanded seconds cross the "
             f"region · {len(dkm)} matched pairs · idle gaps skipped",
             color=INK_2, fontsize=10.5)
    hud = fig.text(0.045, 0.866, "", color=INK, fontsize=11)
    fig.legend(handles=[
        Line2D([], [], ls="none", marker="o", mfc=PRED, mec=SURFACE, ms=7.5,
               label="Predicted (planning orbit, all transmitters)"),
        Line2D([], [], ls="none", marker="o", mfc=ACT, mec=SURFACE, ms=7.5,
               label="Actual (as flown, top-4 channels)"),
        Line2D([], [], color=MUTED, lw=1.2, label="matched pair"),
    ], loc="upper left", bbox_to_anchor=(0.038, 0.845), ncol=3, frameon=False,
        fontsize=9.5, handletextpad=0.3, columnspacing=1.6, labelcolor=INK_2)
    fig.text(0.045, 0.012,
             "Pairs matched by GPS transmitter at each planner-commanded RawIF second · "
             "actual = first 2 Hz sample per second · dashed boxes = fire watchlist areas",
             color=MUTED, fontsize=8)

    # ---------------------------------------------------------- region map
    cx, cy, xspan, yspan = region_extent(region)
    w = 0.655
    h = w * (12.8 / 7.2) * (yspan / xspan)
    if h > 0.775:  # tall region: shrink width to fit the vertical slot
        w *= 0.775 / h
        h = 0.775
    y0 = 0.035 + (0.775 - h) / 2
    axm = fig.add_axes([0.005, y0, w, h])
    axm.set_facecolor(SURFACE)
    axm.set_aspect("equal")
    axm.axis("off")
    axm.set_xlim(cx - xspan / 2, cx + xspan / 2)
    axm.set_ylim(cy - yspan / 2, cy + yspan / 2)
    draw_map_chrome(axm, region)

    if args.exaggerate != 1.0:
        axm.text(0.985, 0.975, f"map offsets exaggerated ×{args.exaggerate:g}",
                 transform=axm.transAxes, ha="right", va="top",
                 color=INK_2, fontsize=9.5, zorder=6)

    lc = LineCollection([], linewidths=1.1, zorder=2.6)
    axm.add_collection(lc)
    sc_pred = axm.scatter([], [], s=46, facecolors=PRED, edgecolors=SURFACE,
                          linewidths=0.6, zorder=3)
    sc_act = axm.scatter([], [], s=46, facecolors=ACT, edgecolors=SURFACE,
                         linewidths=0.6, zorder=4)

    # ------------------------------------------------- dartboard (error, km)
    axd = fig.add_axes([0.675, 0.40, 0.30, 0.44])
    axd.set_facecolor(SURFACE)
    axd.set_aspect("equal", adjustable="box")
    rmax = max(1.0, math.ceil(dkm.max())) if len(dkm) else 1.0
    axd.set_xlim(-rmax * 1.18, rmax * 1.18)
    axd.set_ylim(-rmax * 1.18, rmax * 1.18)
    for sp in axd.spines.values():
        sp.set_visible(False)
    axd.set_xticks([])
    axd.set_yticks([])
    axd.axhline(0, color=GRID, lw=0.7, zorder=1)
    axd.axvline(0, color=GRID, lw=0.7, zorder=1)
    for r in range(1, int(rmax) + 1):
        axd.add_patch(Circle((0, 0), r, fill=False, edgecolor=GRID, lw=0.7, zorder=1))
        axd.text(r * 0.7071, r * 0.7071, f"{r} km" if r == int(rmax) else str(r),
                 color=MUTED, fontsize=8, ha="left", va="bottom", zorder=2)
    axd.set_title("Actual − predicted offset (east/north km)",
                  color=INK_2, fontsize=10.5, loc="left", pad=8)
    sc_dart_old = axd.scatter([], [], s=14, facecolors=MUTED, edgecolors="none",
                              alpha=0.45, zorder=3)
    sc_dart_new = axd.scatter([], [], s=52, facecolors=INK, edgecolors=SURFACE,
                              linewidths=0.6, zorder=4)

    # ------------------------------------------------------- offset vs time
    axs = fig.add_axes([0.705, 0.085, 0.27, 0.235])
    axs.set_facecolor(SURFACE)
    axs.set_xlim(0, 24)
    axs.set_ylim(0, (dkm.max() if len(dkm) else 1.0) * 1.15)
    axs.set_xticks(range(0, 25, 6))
    axs.grid(axis="y", color=GRID, lw=0.7)
    for name, sp in axs.spines.items():
        sp.set_color(BASELINE)
        sp.set_visible(name in ("left", "bottom"))
    axs.tick_params(colors=MUTED, labelsize=8.5, length=3, width=0.7)
    axs.set_xlabel("UTC hour", color=MUTED, fontsize=8.5)
    axs.set_ylabel("offset (km)", color=MUTED, fontsize=8.5)
    axs.set_title("Offset vs time of day", color=INK_2, fontsize=10.5, loc="left", pad=6)
    sc_strip = axs.scatter([], [], s=9, facecolors=MUTED, edgecolors="none", alpha=0.5, zorder=3)

    def rgba(hexcol, alphas):
        r, g, b = (int(hexcol[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
        out = np.empty((len(alphas), 4))
        out[:, 0], out[:, 1], out[:, 2], out[:, 3] = r, g, b, alphas
        return out

    fresh_w = max(2.5, float(args.stride))

    def update(i):
        done = i >= len(times)
        cur = times[-1] if done else times[i]
        vp, va, vl = psec <= cur, asec <= cur, lsec <= cur

        def fade(sec_arr, mask):
            if done:
                return np.full(int(mask.sum()), 0.7)
            age = cur - sec_arr[mask]
            return np.where(age <= 2, 1.0, 0.18 + 0.82 * np.exp(-(age - 2) / 25.0))

        ap, aa, al = fade(psec, vp), fade(asec, va), fade(lsec, vl)
        sc_pred.set_offsets(ppts[vp])
        sc_pred.set_facecolors(rgba(PRED, ap))
        sc_pred.set_edgecolors(rgba(SURFACE, ap))
        sc_act.set_offsets(apts[va])
        sc_act.set_facecolors(rgba(ACT, aa))
        sc_act.set_edgecolors(rgba(SURFACE, aa))
        lc.set_segments(segs[vl])
        lc.set_colors(rgba(MUTED, al * 0.9))

        if done:
            freshl = np.zeros_like(vl)
        else:
            freshl = vl & (lsec > cur - fresh_w)
        sc_dart_old.set_offsets(dart[vl & ~freshl])
        sc_dart_new.set_offsets(dart[freshl] if freshl.any() else np.empty((0, 2)))
        sc_strip.set_offsets(np.column_stack((lhours[vl], dkm[vl])))

        med = np.median(dkm[vl]) if vl.any() else None
        med_txt = f"{med:.1f} km" if med is not None else "—"
        if done:
            p95 = f" · 95th pct {np.percentile(dkm, 95):.1f} km" if len(dkm) else ""
            hud.set_text(f"full day · {n_pass} region passes · median offset {med_txt}{p95}")
        else:
            base = bases[i]
            wp = vp & (psec > cur - fresh_w)
            wa = va & (asec > cur - fresh_w)
            sats = "+".join(str(s) for s in sorted(set(D["psat"][wp]) | set(D["asat"][wa])))
            hud.set_text(f"{int(base) // 3600:02d}:{int(base) % 3600 // 60:02d}:{int(base) % 60:02d} UTC"
                         f" · pass {pass_idx[base]}/{n_pass} · CYG{sats or '—'}"
                         f" · median offset so far {med_txt}")
        return sc_pred, sc_act, lc, sc_dart_old, sc_dart_new, sc_strip, hud

    return fig, update, n_frames, rate


def make_map_figure(day, D, region, args):
    """Static full-day map — just the points on the region map, with larger
    slide-ready type. Sized to the region's aspect (capped at 10 in tall)."""
    ppts = np.column_stack(albers(D["plat"], D["plon"])) if len(D["psec"]) else np.zeros((0, 2))
    apts = np.column_stack(albers(D["alat"], D["alon"])) if len(D["asec"]) else np.zeros((0, 2))
    if args.exaggerate != 1.0 and len(D["lp"]):
        apts[D["la"]] = ppts[D["lp"]] + args.exaggerate * (apts[D["la"]] - ppts[D["lp"]])
    segs = (np.stack((ppts[D["lp"]], apts[D["la"]]), axis=1)
            if len(D["lp"]) else np.zeros((0, 2, 2)))

    cx, cy, xspan, yspan = region_extent(region)
    fig_w, w_frac, head_in, bot_in, max_h = 9.6, 0.968, 1.62, 0.22, 10.0
    map_h_in = fig_w * w_frac * yspan / xspan
    fig_h = head_in + map_h_in + bot_in
    hk = 1.0
    if fig_h > max_h:  # tall region: shrink to a slide-friendly height
        k = (max_h - head_in - bot_in) / map_h_in
        fig_w, map_h_in, fig_h = fig_w * k, map_h_in * k, max_h
        hk = k  # header text scales with the width so it never overflows
    fig = plt.figure(figsize=(fig_w, fig_h), dpi=args.dpi)
    fig.patch.set_facecolor(SURFACE)
    dash = f"{day[:4]}-{day[4:6]}-{day[6:]}"

    def yf(inches_from_top):
        return 1.0 - inches_from_top / fig_h

    fig.text(0.03, yf(0.40), "CYGNSS RawIF: predicted vs actual specular points",
             color=INK, fontsize=19 * hk, fontweight="bold")
    med = np.median(D["dkm"]) if len(D["dkm"]) else None
    fig.text(0.03, yf(0.70), f"{dash} · {region['label']}",
             color=INK_2, fontsize=12.5 * hk)
    fig.text(0.03, yf(0.96),
             f"{len(D['psec'])} predicted / {len(D['asec'])} actual points at "
             f"planner-commanded RawIF seconds"
             + (f" · median offset {med:.1f} km" if med is not None else ""),
             color=INK_2, fontsize=12.5 * hk)
    fig.legend(handles=[
        Line2D([], [], ls="none", marker="o", mfc=PRED, mec=SURFACE, ms=11 * hk,
               label="Predicted (planning orbit)"),
        Line2D([], [], ls="none", marker="o", mfc=ACT, mec=SURFACE, ms=11 * hk,
               label="Actual (as flown)"),
        Line2D([], [], color=MUTED, lw=1.8, label="matched pair"),
    ], loc="upper left", bbox_to_anchor=(0.022, yf(1.06)), ncol=3, frameon=False,
        fontsize=12.5 * hk, handletextpad=0.35, columnspacing=1.8, labelcolor=INK_2)

    axm = fig.add_axes([(1 - w_frac) / 2, bot_in / fig_h, w_frac, map_h_in / fig_h])
    axm.set_facecolor(SURFACE)
    axm.set_aspect("equal")
    axm.axis("off")
    axm.set_xlim(cx - xspan / 2, cx + xspan / 2)
    axm.set_ylim(cy - yspan / 2, cy + yspan / 2)
    draw_map_chrome(axm, region, fs=1.45)
    if args.exaggerate != 1.0:
        axm.text(0.985, 0.982, f"map offsets exaggerated ×{args.exaggerate:g}",
                 transform=axm.transAxes, ha="right", va="top",
                 color=INK_2, fontsize=13, zorder=6)

    axm.add_collection(LineCollection(segs, colors=MUTED, alpha=0.85,
                                      linewidths=1.5, zorder=2.6))
    axm.scatter(ppts[:, 0], ppts[:, 1], s=120, facecolors=PRED,
                edgecolors=SURFACE, linewidths=1.0, zorder=3)
    axm.scatter(apts[:, 0], apts[:, 1], s=120, facecolors=ACT,
                edgecolors=SURFACE, linewidths=1.0, zorder=4)
    return fig


# ------------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("day", nargs="?", default="20260702", help="YYYYMMDD (default 20260702)")
    ap.add_argument("--fire", default="Pocket",
                    help="watchlist fire to centre the region on (default Pocket)")
    ap.add_argument("--pad", type=float, default=0.4,
                    help="degrees of margin around the fire box (default 0.4)")
    ap.add_argument("--bbox", nargs=4, type=float,
                    metavar=("LATMIN", "LATMAX", "LONMIN", "LONMAX"),
                    help="explicit region bounds in degrees; overrides --fire/--pad")
    ap.add_argument("--rate", type=float,
                    help="data-seconds per video second (default: auto, ~20 s clip)")
    ap.add_argument("--exaggerate", type=float, default=1.0, metavar="N",
                    help="stretch matched actual dots N x their true offset from the "
                         "predicted partner on the map (annotated; default 1 = true scale)")
    ap.add_argument("--gif", action="store_true", help="write animated GIF instead of MP4")
    ap.add_argument("--fps", type=int, default=None, help="frames/s (default 24 mp4, 12 gif)")
    ap.add_argument("--stride", type=int, default=1,
                    help="animate every Nth commanded second (default 1)")
    ap.add_argument("--dpi", type=int, default=None,
                    help="figure dpi (default 100 -> 1280x720 video; 200 for --map)")
    ap.add_argument("--out", help="output path (default helper_scripts/output/...)")
    ap.add_argument("--map", action="store_true",
                    help="write one high-quality full-day map PNG (points only, "
                         "larger slide-ready type) instead of an animation")
    ap.add_argument("--preview", action="store_true",
                    help="write 3 still PNGs (early/mid/final) and exit, no video")
    args = ap.parse_args()
    args.fps = args.fps or (12 if args.gif else 24)
    args.dpi = args.dpi or (200 if args.map else 100)

    day = args.day
    for req in (f"planner/output/{day}", f"orbits/output/{day}"):
        if not os.path.isdir(f"{BASE}/{req}"):
            sys.exit(f"missing {req}")
    dash = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    if not os.path.exists(f"{BASE}/orbits-actual/specular_trajectory_{dash}.csv"):
        sys.exit(f"missing orbits-actual/specular_trajectory_{dash}.csv "
                 "(actual data covers 2026-06-30..2026-07-11)")

    if args.bbox:
        la0, la1, lo0, lo1 = args.bbox
        if la0 >= la1 or lo0 >= lo1:
            sys.exit("--bbox wants LATMIN < LATMAX and LONMIN < LONMAX")
        slug = "bbox"
    else:
        fla0, fla1, flo0, flo1 = fire_box(day, args.fire)
        la0, la1 = fla0 - args.pad, fla1 + args.pad
        lo0, lo1 = flo0 - args.pad, flo1 + args.pad
        slug = args.fire.lower()
    if args.exaggerate != 1.0:
        slug += f"_x{args.exaggerate:g}"
    if lo1 < 0:
        lon_txt = f"{abs(lo1):.2f}–{abs(lo0):.2f}°W"
    else:
        lon_txt = f"{lo0:.2f}–{lo1:.2f}°E"
    label = (f"{'custom region' if args.bbox else args.fire + ' fire region'} "
             f"({la0:.2f}–{la1:.2f}°N, {lon_txt})")
    region = {"bbox": (la0, la1, lo0, lo1), "label": label, "fires": load_day_fires(day)}

    print(f"[{day}] region: {label}")
    print(f"[{day}] reading plans ...")
    plans = load_plans(day)
    n_cmd = sum(len(v) for v in plans.values())
    print(f"  {len(plans)} satellites, {n_cmd} commanded RawIF seconds")
    print(f"[{day}] reading predicted specular points (7 x ~1.5M rows) ...")
    pred = load_predicted(day, plans)
    print(f"[{day}] reading actual trajectories ...")
    act = load_actual(day, plans)
    D = build_dataset(pred, act, region["bbox"])
    if len(D["psec"]) == 0 and len(D["asec"]) == 0:
        sys.exit("no predicted or actual points fall inside the region on this day")
    print(f"  in region: {len(D['psec'])} predicted pts, {len(D['asec'])} actual pts, "
          f"{len(D['dkm'])} matched pairs"
          + (f"; offset median {np.median(D['dkm']):.2f} km, max {D['dkm'].max():.2f} km"
             if len(D["dkm"]) else ""))

    outdir = f"{BASE}/helper_scripts/output"
    os.makedirs(outdir, exist_ok=True)

    if args.map:
        fig = make_map_figure(day, D, region, args)
        out = args.out or f"{outdir}/rawif_pred_vs_actual_{day}_{slug}_map.png"
        fig.savefig(out, dpi=args.dpi, facecolor=SURFACE)
        print(f"done: {out} ({os.path.getsize(out) / 1e6:.1f} MB)")
        return

    fig, update, n_frames, rate = make_animation(day, D, region, args)

    ext = "gif" if args.gif else "mp4"
    out = args.out or f"{outdir}/rawif_pred_vs_actual_{day}_{slug}.{ext}"

    if args.preview:
        for tag, i in (("early", n_frames // 8), ("mid", n_frames // 2), ("final", n_frames - 1)):
            update(i)
            p = f"{outdir}/rawif_pred_vs_actual_{day}_{slug}_{tag}.png"
            fig.savefig(p, dpi=args.dpi, facecolor=SURFACE)
            print(f"  wrote {p}")
        return

    if args.gif:
        writer = PillowWriter(fps=args.fps)
    else:
        import imageio_ffmpeg
        matplotlib.rcParams["animation.ffmpeg_path"] = imageio_ffmpeg.get_ffmpeg_exe()
        writer = FFMpegWriter(fps=args.fps, codec="libx264", extra_args=[
            "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "medium",
            "-vf", "crop=trunc(iw/2)*2:trunc(ih/2)*2"])

    print(f"[{day}] rendering {n_frames} frames -> {out} "
          f"({n_frames / args.fps:.0f} s at {args.fps} fps, {rate:.1f} data-s per video s)")
    anim = FuncAnimation(fig, update, frames=n_frames, interval=1000 / args.fps)
    last_pct = -1

    def progress(i, n):
        nonlocal last_pct
        pct = 100 * (i + 1) // n
        if pct // 5 > last_pct // 5:
            print(f"  {pct}% ({i + 1}/{n})", flush=True)
            last_pct = pct

    anim.save(out, writer=writer, dpi=args.dpi, progress_callback=progress)
    update(n_frames - 1)
    poster = os.path.splitext(out)[0] + "_poster.png"
    fig.savefig(poster, dpi=args.dpi, facecolor=SURFACE)
    size = os.path.getsize(out) / 1e6
    print(f"done: {out} ({size:.1f} MB) + poster {poster}\n"
          "PowerPoint: Insert -> Video -> This Device"
          + (" (GIF: Insert -> Pictures)" if args.gif else ""))


if __name__ == "__main__":
    main()
