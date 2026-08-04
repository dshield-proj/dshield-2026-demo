# DShield 2026 fire demo

Working notes for this folder. Work done July 13–21 and Aug 4, 2026
(Claude-assisted):
analysis and visualization of the burned-area tasking campaign, plus the
fire-danger (WFPI Day-1), soil-moisture, and RawIF-sweep (planner ×
orbits-actual) layers. `orbits/` holds the **predicted** orbits the plan was
built on; `orbits-actual/` holds the **actual** orbits. The console uses
`planner/output`'s `*_plan.csv` + `orbits-actual/`; `*_choices.txt`,
`orbits/Grid.csv`, and the TV files are analyzed and documented below but not
used by the console; solver internals and the per-satellite `orbits/output`
access predictions are unanalyzed (the specular predictions are analyzed —
see the PPT-animation section below). All data facts below were re-verified
against the on-disk data 2026-08-04 — after the 2026-07-17 orbits-actual
extension and the 2026-07-21 burned-area refresh, which had invalidated
several earlier figures.

## What was built

### `fire-console/` — served web console (the main deliverable)

Interactive fire-watch console served by a **stdlib-only Python server** that
reads the data below live from disk (nothing baked into the HTML). Run
`python3 fire-console/server.py` → http://localhost:8000; view from a laptop
via SSH port forwarding (see `fire-console/README.md`). Optional `--from` /
`--to` flags (added 2026-08-04; YYYY-MM-DD or YYYYMMDD, inclusive) clamp the
served day window — e.g.
`python3 fire-console/server.py --from 2026-06-30 --to 2026-07-10` — filtering
the config/soil/RawIF day lists via `_in_window`, so every layer follows;
default is all days on disk. Features: Albers CONUS
map with daily tasking footprints, AZ/NM + FL zoom insets with CYGNSS detection
dots (filled red = burned, hollow = unburned, opacity = classifier confidence),
play/scrub timeline, per-fire detail overlay with day/cumulative modes,
burn-fraction trend chart, watchlist persistence Gantt. The page polls
`/api/version` every 5 s — day folders added/removed on disk appear
automatically. Added 2026-07-14: toggleable **WFPI Day-1 fire-danger
backdrop** on the CONUS map + insets (`/api/danger` serves transparent
indexed PNGs warped server-side into the map's Albers space — pure-stdlib
TIFF parse, warp-index cache, PNG cache by mtime) and a per-fire box-mean
danger index in the bundle (`fires[].danger`), roster, tooltips, and detail.
Added 2026-07-14: **soil-moisture card** — the two fixed retrieval sites, each
with a day-following retrieved-field thumbnail (dry→wet colormap, `/api/soil`)
and a daily area-mean sparkline; sites also marked on the CONUS map. Bundle
`soil` carries per-area daily-mean series, value domain, units, and colormap.
Added 2026-07-15: toggleable **RawIF sweep** — a canvas overlay on the CONUS
map animates each day's commanded RawIF captures as black dots (light ink in
dark theme) at the *actual* specular-point locations: a clock sweeps the UTC
day at 1440× with a ~25 min trailing fade, idle gaps between passes are
skipped, and a HUD pill shows the sweep time + pass n/m plus a transport:
pause/resume, prev/next pass, ±15 min rewind/fast-forward, a scrub slider
over the day's passes (idle gaps compressed out of the slider's range), and
a speed cycle ¼×–4× on the base 1440× rate (paused state and speed persist
across day changes for the session). Added 2026-07-15: each satellite's
newest visible dot carries a small **NORAD tag** (canvas text, halo'd in
`--surface`; one tag per sat, not per channel). `/api/rawif?day=`
joins the planner's per-satellite RawIF seconds to the actual 2 Hz
trajectories (all 4 channels, first sample per sat-second, ~3.9–4.6k
pts/day measured 2026-08-04 — an earlier ~69k figure was stale) and
serves gzipped time-sorted arrays — `t/lat/lon` plus per-point sat index `s`
into `sats` (NORAD ids) — cached by input mtimes (~1.2 s cold; the
fingerprint carries an extraction-version salt `_RAWIF_VER`, currently
`fleet-v1` — bump it if the extraction logic changes, or stale browser
caches survive). Bundle `rawif.days` maps day → cache token.
Added 2026-07-16: **fleet ops strip** under the CONUS map (shows/hides with
the RawIF layer) — 7 per-satellite storage bars + 3 ground-station boxes
(AUS/HI/CHI) that light ember and name the transmitting sat during `DNL:`
windows. `/api/rawif` payload gained a `storage` block: per-sat
piecewise-linear storage-% breakpoints (`lv[i]={t,v}`, simulated from the
plan: +100/60 % per RawIF obs — 60-image buffer — and −100/1200 % per DNL
second — 20 min full drain — clamped 0–100, buffer starts empty at 00:00
UTC each day) and `dnl=[[t0,t1,satIdx,stationIdx],…]`. The client folds DNL
windows into the sweep's pass segmentation (they mostly fall *between*
capture passes, which the sweep would otherwise skip), roughly doubling the
per-day loop (~45–50 s wall at 1×); the HUD appends "▼ downlink" while any
window is active.
Added 2026-07-21: collapsible **workflow banner** between the header and the
map — one-line pipeline summary + hand-rolled SVG of the demo's module flow
(from `Presentation1.pdf`: CYGNSS L1 + active-fire priority → burned-area /
soil-moisture → fire danger → planner, with orbits feeding in; WRF-SFIRE and
next-day commands as dashed "ghost" nodes). Nodes tooltip their role /
deployment site / daily UTC slot and click-jump to the console view showing
their product (danger & planner nodes also switch the map layer on).
Open/closed persists in `localStorage["fire-workflow"]` (default open).

⚠️ A **WFPI Day-1 backdrop on the soil-site thumbnails** (danger raster under
the retrieval pixels via the existing `/api/danger?view=geo` warp at the soil
bbox, tied to the danger-layer toggle) was built 2026-07-21 and **reverted on
request** the same day — do not re-add it unless asked. If it ever comes
back: the rasters share the 1 km LAEA grid so bbox alignment is exact, but
mute the backdrop to ~0.5 opacity — at the map's 0.66 the soil dots drown in
NM's red danger bins (verified with server-side composites).

⚠️ **Deliberately unfiltered** (user decision 2026-07-15): all 4 channels are
drawn, including specular points outside CONUS (Gulf, Mexico, ocean) — a
commanded second captures the whole receiver, and the user wants the
as-flown picture. A target-matched variant (keep only channels within 50 km
of the second's intended GPs from `*_choices.txt` × `orbits/Grid.csv`) was
built and then reverted on request — do not re-add it, or any CONUS
clipping, unless asked.

⚠️ A **NIFC-perimeter layer in the per-fire detail overlay** (dashed outline
from `daily_fire_perimeters/` + `/api/perimeter` endpoint + bundle
`perims.days`) was built 2026-07-15 and then **reverted on request** the same
day — do not re-add it unless asked. The `daily_fire_perimeters/` data folder
itself is kept; it is just not wired into the console.

An earlier **static snapshot** (data embedded, no server) is published as a
claude.ai artifact: https://claude.ai/code/artifact/0a122e8b-5fd7-4945-9938-7d2b09b34f90

### `helper_scripts/rawif_pred_vs_actual_anim.py` — PPT animation (2026-07-17, reworked 2026-07-18)

Per-day MP4 (or `--gif`) for PowerPoint comparing **predicted vs actual**
specular points at the planner-commanded RawIF seconds, in a **lat/lon
bounded region** — default the Pocket fire box + 0.4° pad (`--fire` picks
another watchlist fire, `--bbox LATMIN LATMAX LONMIN LONMAX` is explicit).
Map draws *all* predicted points (every visible transmitter) as filled blue
dots, actual top-4 points as filled green dots, and a connector line per
matched pair, over fire watchlist boxes + graticule + km scale bar; side
panels: actual−predicted offset "dartboard" in km + offset-vs-UTC strip;
recent bright, history faint, idle gaps skipped, pacing auto-slowed for
sparse regions (`--rate` = data-s per video s). ⚠️ Offsets (~1–4 km) are
sub-pixel at region scale, so matched green dots sit exactly on their blue
partners — `--exaggerate N` stretches actual dots N× from their partner on
the map (annotated on-figure; dartboard/strip stay true). `--map` writes a
single high-quality full-day PNG instead — just the region map + points
with larger slide-ready type (figure sized to region aspect, 200 dpi
default). Needs
numpy/matplotlib/imageio-ffmpeg (pip-installed 2026-07-17; NOT stdlib-only
like the console). `--preview` writes 3 stills; outputs land in
`helper_scripts/output/` (untracked) with a poster PNG. Pairs are matched by
GPS transmitter per (sat, second) — prediction lists all visible
transmitters, actual only the top-4 by signal strength, so matching is done
from the actual side (100 % matched on 20260702; a point just outside the
region is kept when its partner is inside).

Predicted specular format (`orbits/output/YYYYMMDD/CYG<norad>/specular/
specular.csv`, ~47 MB/sat/day): 5 header lines then
`time index,source id,lat [deg],lon [deg],rank` — time index = second-of-day
0…86400, `source id` = `GNSS<gps-norad>` (matches `norad_chN` in
`orbits-actual`), **lon is 0–360** (normalize!), `rank` is always empty, rows
are **grouped by transmitter, not time-sorted** (never early-break a scan).
Measured 2026-07-02: offsets median 1.4 km / max 3.8 km — early-day passes sit
~1.3 km east of prediction, the late-day passes (after the day's 14 h idle
stretch) ~2–3.5 km west, i.e. drift grows with propagation age. Actual
trajectories have **empty channel fields** — ~22 % of rows (~270k/day) are
missing at least one channel (an earlier "~300/day" figure counted only the
channel samples lost at the commanded RawIF seconds) — guard the parse.

## Data layout and facts

### `dshield-demo-configuration/burned-area-config/YYYYMMDD/fires.json`

Daily tasking watchlists — **exactly 5 fires/day** prioritized for burned-area
satellite observation. Current analysis window: **2026-06-29 → 2026-07-10
(12 days)** — the 2026-07-11…13 folders were deliberately removed on
2026-07-14 to trim the analysis period. 8 unique fires by `irwin_id`, each a
~0.5°×0.6° lat/lon box. Sycamore, Shell, Pocket held all 12 days; Sacaton 9;
the 5th slot churns (STEAMBOAT → White_Tail → Avocado). Two clusters:
Arizona/New Mexico ("Southwest") and Florida ("Southeast").

**These config boxes are the source of truth.** The copies at
`burned-area/output/*/fires.json` have data-derived extents that differ
slightly from the config boxes (the pre-2026-07-21 copies also carried
`-999.0` sentinel values) — don't use them.

Region naming gotcha: naive "longitude < -100 ⇒ Arizona" mislabels the
New Mexico fire (Sacaton, lon −108.7); use Southwest/Southeast region names.

### `burned-area/output/YYYYMMDD/burned_<FireName>.csv`

CYGNSS L1 burned-area classifications, one file per fire-day. The columns that
matter for mapping: `sp_lat`, `sp_lon` (specular point), `y_pred` (0/1 burned),
`y_uncert` (0–0.78). ⚠️ **The CSVs were all replaced 2026-07-21** (upstream
re-sync): now 12,043 records over the window, ~18% classified burned, and
**no −999.0 sentinel coordinates** (the earlier data had ~11.3k records,
~51% burned, and 168 sentinel rows — keep the coordinate filter as a guard
anyway; the console still applies it).

⚠️ These CSVs embed multi-line reflectivity matrices in quoted fields (files
are 1.6–23 MB, ~810 MB total). Never count records with `wc -l`; parse with
Python `csv` after `csv.field_size_limit(10**7)`.

`prediction_info.txt` per day records the original processing paths.
`sample/` holds one older sample day (20260529).

### `fire-danger/output/YYYYMMDD/{wlfp,wfpi,wfsp}_YYYYMMDD_DayN.zip`

USGS fire-danger forecast rasters, days **20260629 → 20260709 only** (no
20260710 — the console shows "no WFPI Day-1 product" there). ⚠️ For a given
day+lead the three product zips (wlfp/wfpi/wfsp) are **byte-identical**, and
the tif inside is always named `wfpi_*` — the demo packaged one raster under
all three names. The console reads `wfpi_*_Day1.zip` (switched from wlfp on
2026-07-14; identical bytes either way). Day1 files do differ across dates. Each zip holds one
GeoTIFF: 4587×2889, 8-bit, uncompressed strips, palette embedded, 1 km USGS
CONUS Lambert Azimuthal Equal-Area *sphere* grid (R=6370997, center 45N/100W,
upper-left −2051000, 753000). Values 0–150 = fire-potential index (13 palette
bins, dark green → red); 249–254 = land-cover specials (barren/urban/ag/
snow/water); 255 nodata. Stdlib-parseable — see `_parse_tif` in
`fire-console/server.py`.

### `soil-moisture/output/YYYYMMDD/soil_moisture_area_id_N.tif`

Retrieved soil moisture at **two fixed monitoring areas** (boxes in
`dshield-demo-configuration/soil-moisture-config/YYYYMMDD/sm_areas.json`):
`area_id 1` = **NM** (32.22–32.75 N, −107.16…−106.07), `area_id 2` =
**TX_panhandle** (35.14–36.59 N, −102.76…−99.32). Boxes are constant across
days; only the rolling `prod_start/end_date` window changes. Output days
**20260629 → 20260710** (12 days, matches the burned-area window). Each tif is a
**32-bit IEEE-float, DEFLATE-compressed, tiled (256×256)** GeoTIFF on the *same*
USGS CONUS LAEA-sphere 1 km grid as the danger rasters (so `laea_grid`/
`laea_inv`/`_warp_idx` are reused). NODATA = `-9999` marks pixels outside the
retrieved footprint (the footprint is a small fraction of the box — NM has
~95–266 valid px of 106×66 depending on the day). Values are volumetric
soil moisture ≈ 0.05–0.15 m³/m³.
⚠️ Not the same layout as the danger tif — use `_parse_soil_tif` (handles
tiles + deflate + float), not `_parse_tif`. `sample/output/soil_moisture.tif`
is one example area tile.

### `planner/output/YYYYMMDD/` — RawIF command plans & targets

Per-satellite planner outputs, days **20260630 → 20260711**, 7 CYGNSS
satellites (NORAD 41884–88, 41890–91; no 41889). The plan was optimized
against the **predicted** orbits in `orbits/` (per-day access/specular/
propagation folders per satellite), *not* the actual orbits.

- `CYG<norad>_plan.csv`: two `#` comment lines, then `second_of_day, Command`
  rows — `RawIF` (69–226 s/sat/day, measured 2026-08-04: 113–226 within
  20260630–0709, the 0710/0711 plans dip to 69; an earlier note claiming
  2.4–2.8k was stale) plus `DNL: <station>` downlink rows
  (~1.6–3.9k s/sat/day, stations AUS/HI/CHI, 7–16 contiguous windows/sat/day).
  Storage rates used by the demo: one obs fills 100/60 % of the 60-image
  buffer; one downlink second frees 100/1200 % (20 min empties a full
  buffer). Simulated per day from empty, buffers hit 100 % and the planner
  over-provisions downlink (drains clamp at 0 often). No same-second
  obs∩dnl collisions; no station ever receives two sats at once in this
  window.
- `CYG<norad>_choices.txt`: `sec: [{'cmd': 'obs', 'targets': [gp, ...]}]` —
  the target **grid-point ids** each candidate second would observe (Python
  literal syntax; only `cmd == 'obs'` entries; every RawIF-commanded second
  has one). GP ids index `orbits/Grid.csv` (`GP index,lat,lon`; **114,454
  GPs**, southern-CONUS band lat 22.6–40, lon −128.5…−65.5 — extends slightly
  into northern Mexico). `dshieldFire.lp`, `solution.sol`, logs = solver
  internals, unanalyzed. Target values from
  `dshield-demo-configuration/active-fire-priority-proxy/*/TV_ACTIVE_FIRE.csv`
  (`GP index,values` header + 114,454 GP rows).

### `daily_fire_perimeters/YYYYMMDD/<Fire>.geojson`

NIFC WFIGS perimeters (re-fetched live 2026-07-17 from
`WFIGS_Daily_Perimeters_Public`, matched by `irwin_id`; the 2026-07-15 fetch
had been deleted) for all 8 burned-area fires, dates **20260629 → 20260710**.
Regenerate with `helper_scripts/fetch_fire_perimeters.py` (stdlib-only).
Per date: latest record with `poly_DateCurrent` ≤ end of that UTC day;
carried forward when stale (`properties.carried_forward`/`days_stale`;
`manifest.csv` indexes all date × fire statuses). ⚠️ Rookery has **no polygon
in any NIFC service** — only `Rookery_incident_point.geojson`; White_Tail's
single perimeter starts 07-02; Avocado's starts 07-08; Shell's is ~20–31 days
stale (contained, last record 06-09). Don't trust `poly_PolygonDateTime`
(nulls + year typos) — see the folder README.

### Root sync scripts, R2 buckets, and docs (verified 2026-08-04)

The demo's modules ran on different machines and exchanged all data via
**Cloudflare R2 buckets**; the root scripts sync them ↔ local disk.
`README.md` was rewritten 2026-08-04 (project overview + fire-console run
section on top; sync docs below verified against the scripts). Facts:

- `sync_read_all.py` downloads **only the `read_only` buckets** in
  `cloudflare_r2.json` (rclone copy — remotes never modified, local extras
  kept); read-write buckets are left untouched, and are seeded from remote
  on the first `sync_rw.py` run if the local dir is missing. `sync_rw.py`
  uploads via rclone copy (no remote deletes) unless `--delete` (rclone
  sync). `setup.py` writes the `r2-rw`/`r2-ro` remotes into
  `~/.config/rclone/rclone.conf`, preserving unrelated remotes.
  `cloudflare_r2.json` is gitignored; format in `cloudflare_r2_template.json`.
- The **fire-arrival and pre-fire-priority buckets exist on Cloudflare but
  may not be synced locally** (user, 2026-08-04) — their absence on a given
  machine is expected; don't flag it or drop them from docs.
- Naming gotchas: the config bucket's planner folder is `planner_config`
  (underscore; files inside are `planner-config-YYYYMMDD.json`), vs
  `burned-area-config`/`soil-moisture-config` (hyphens). Local folder and
  config paths use `burned-area` (hyphen), not `burned_area`.
- Daily master configs `dshield-demo-configuration/
  dshield-demo-config-YYYYMMDD.json` (20260618 → 20260714) map each
  component to its output path; **orbits/planner outputs go to the *next*
  day** (e.g. the 20260702 config writes `orbits/output/20260703/`).
- `demo_overview.pdf` (repo root, committed 2026-08-04): 3-page
  NASA/ESTO demo deck — goals (automated observe→process→predict→task
  pipeline, TRL 5 exit), the 12-day window, 7-sat CYGNSS (commands produced
  but not executed), distributed execution over R2, module-flow diagram and
  daily UTC timeline (00 UTC active-fire priority proxy; soil-moisture +
  burned-area until ~14 UTC; orbits 14 UTC; planner 17 UTC; fire-danger +
  pre-fire priority 19 UTC; fire-arrival/WRF-SFIRE run post-demo). Same
  content family as the `Presentation1.pdf` behind the workflow banner.

### `orbits-actual/specular_trajectory_YYYY-MM-DD.csv`

**Actual** specular-point trajectories (vs the predicted `orbits/` used for
planning), days **2026-06-30 → 2026-07-11** (~170 MB, ~1.19M rows/day; the
07-10 and 07-11 files landed 2026-07-17, after the original notes).
Columns: `spacecraft, cygnss_norad, time` (UTC timestamp at 2 Hz, fixed
layout — parse HH:MM:SS by slicing chars 11:19) then
`sp_lat_chN, sp_lon_chN, norad_chN` for channels 1–4 (norad_chN = the GPS
transmitter, not the CYGNSS sat). Join to plans on `cygnss_norad` +
floor(second-of-day). RawIF-animatable days = plans ∩ trajectories =
**20260630 → 20260711** (of the console's config-day window only 06-29
shows "no plan/trajectory" in the HUD; 07-11 has RawIF data but sits
outside the console timeline, which ends at the last config day 07-10).

Facts worth keeping (measured 2026-07-15): a commanded RawIF second captures
**all 4 channels**, but only some sit over the intended targets — the rest
land hundreds of km away in the swath (Gulf, Mexico, ocean; all points
within lat 17–42, lon −133…−64). Channel-to-target distance is sharply
**bimodal**: tasked channels ≤ ~18 km from a target GP, others ≥ ~400 km,
with the 35–75 km band empty — so a 50 km cut cleanly separates them if
target matching is ever wanted. At a 50 km cut, ~83% of commanded
sat-seconds have ≥1 channel on target; the misses are actual-vs-predicted
orbit drift. **The console draws all 4 channels unfiltered** (see the RawIF
sweep note above).

## Conventions established (fire-console UI)

- Region palette blue (Southwest) / green (Southeast) / magenta (West Coast) —
  validated colorblind-safe in light and dark themes.
- Ember orange is reserved for "current day" chrome (playhead, active rings);
  status red `--burn` is reserved for the burned classification. Neither is
  ever used as a data-series color. Soil moisture has its own teal identity
  (`--soil`) + a dry→wet sequential colormap (server-side, `soil_colormap`).
  RawIF sweep dots are neutral ink (`--rawif`: near-black in light theme,
  light cream in dark — a deliberate flip, identity carried by the legend +
  HUD, never by color alone).
- Server endpoints: `/api/version` (change fingerprint), `/api/bundle`
  (full model incl. per-fire `[n_obs, n_burned]` day series + `soil` +
  `rawif.days`), `/api/detections?day=&fire=` (compact lat/lon/y/u arrays,
  mtime-cached), `/api/danger?day=&view=` and `/api/soil?day=&area=&bbox=&w=`
  (raster PNGs), `/api/rawif?day=` (gzipped time-sorted t/lat/lon/s arrays + `sats` NORAD list).
- The RawIF sweep is a `<canvas>` overlay pinned over the CONUS svg (same
  960×590 viewBox space, pointer-events none), drawn with pre-rendered dot
  sprites — SVG circles would choke at ~8k dots/frame.
