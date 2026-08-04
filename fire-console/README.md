# Fire Console — burned-area tasking & detections

A self-hosted web console for the DShield burned-area demo. A small stdlib-only
Python server reads the tasking configs and CYGNSS detection CSVs **live from
the local drive** and serves both a JSON API and the interactive page — no data
is baked into the HTML. Drop a new `YYYYMMDD` folder on disk and the page's
timeline grows within ~5 seconds; delete one and it shrinks.

## Run

```bash
cd fire-console
python3 server.py            # → http://localhost:8000
```

No dependencies. Options via environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `8000` | listen port |
| `FIRE_CONFIG_ROOT` | `../dshield-demo-configuration/burned-area-config` | daily `fires.json` tasking configs (source of truth for boxes/watchlist) |
| `FIRE_DETECT_ROOT` | `../burned-area/output` | daily `burned_<Fire>.csv` detection files |
| `FIRE_DANGER_ROOT` | `../fire-danger/output` | daily `wfpi_YYYYMMDD_Day1.zip` fire-danger GeoTIFFs |
| `FIRE_SOIL_ROOT` | `../soil-moisture/output` | daily `soil_moisture_area_id_N.tif` retrievals (two fixed areas) |
| `FIRE_SOIL_CONFIG_ROOT` | `../dshield-demo-configuration/soil-moisture-config` | `sm_areas.json` area boxes |
| `FIRE_PLAN_ROOT` | `../planner/output` | daily `CYG<norad>_plan.csv` RawIF command plans |
| `FIRE_ORBIT_ROOT` | `../orbits-actual` | `specular_trajectory_YYYY-MM-DD.csv` 2 Hz *actual* specular-point trajectories |
| `FIRE_TOKEN` | *(empty = no auth)* | optional; not needed over an SSH tunnel. If set, every request must carry `?token=...` or `Authorization: Bearer ...` |

## Viewing from your laptop (SSH tunnel to the EC2 machine)

The server runs on the EC2 instance and listens on `localhost:8000` there.
Forward that port over SSH and browse it from your laptop — no security-group
changes, no public exposure, and no `FIRE_TOKEN` needed since all traffic is
encrypted inside SSH.

**Option A — VS Code (if connected via Remote-SSH).** Open the **Ports** panel
(bottom bar, next to Terminal, or `Ctrl+Shift+P` → "Forward a Port"), add port
**8000**, then click the forwarded address to open it in your browser. VS Code
often auto-detects the listening port and offers to forward it.

**Option B — plain SSH port forwarding.** From a terminal on your laptop:

```bash
ssh -L 8000:localhost:8000 ubuntu@<ec2-host> -i <your-key.pem>
```

Then open <http://localhost:8000> in your laptop's browser. The tunnel lives as
long as the SSH session stays open (use `-N` for a tunnel-only session with no
shell). If port 8000 is taken on your laptop, remap the local side —
`-L 9000:localhost:8000` — and browse `http://localhost:9000` instead.

Live updates work through the tunnel unchanged: the page polls the server every
5 s, so a new day folder landing on the EC2 disk shows up on your laptop
automatically.

## API

| Endpoint | Returns |
|---|---|
| `GET /api/version` | 12-char fingerprint of all configs + CSV mtimes (the page polls this every 5 s) |
| `GET /api/bundle` | full model: dates, per-day watchlists with obs counts, unique fires with per-day `[n_obs, n_burned]` series |
| `GET /api/detections?day=YYYYMMDD&fire=Name` | compact arrays `{lat, lon, y, u}` for one fire-day |
| `GET /api/danger?day=YYYYMMDD&view=conus` | WFPI Day-1 danger raster as a transparent indexed PNG, warped into the console's Albers map space (rect published in the bundle's `danger.rect`) |
| `GET /api/danger?day=YYYYMMDD&view=geo&bbox=lonMin,lonMax,latMin,latMax&w=600` | same raster over a plain lon/lat rectangle (used by the zoom insets) |
| `GET /api/soil?day=YYYYMMDD&area=N&bbox=lonMin,lonMax,latMin,latMax&w=320` | soil-moisture retrieval for one fixed area warped into a lon/lat rectangle, as a transparent indexed PNG (dry→wet colormap) |
| `GET /api/rawif?day=YYYYMMDD` | one day's RawIF track: time-sorted arrays `{t, lat, lon, s}` (t = UTC second of day; `s` = per-point index into `sats`, the NORAD id list) of all 4 channels' actual specular points at the planner's commanded RawIF seconds, unfiltered; plus a `storage` block — per-satellite storage-% breakpoints `lv[i] = {t, v}` simulated from the plan (+1/60 buffer per obs, −1/1200 per downlink second, clamped 0–100) and downlink windows `dnl = [[t0, t1, satIdx, stationIdx], …]` over `stations` (AUS/HI/CHI); served gzipped |
| `GET /` , `GET /static/conus.json` | the page and the CONUS state outlines |

Parsed CSVs are cached in memory keyed by file mtime, so the multi-MB files are
only re-read when they change. Records with `-999` sentinel coordinates are
excluded as a guard (none in the current data — the 2026-07-21 refresh of the
detection CSVs removed the 168 present earlier).

The danger rasters are read from `wfpi_YYYYMMDD_Day1.zip` (one 8-bit palette
GeoTIFF each, 1 km USGS LAEA CONUS grid) with a stdlib-only TIFF parser and
re-projected server-side through a cached warp-index map; rendered PNGs are
cached by file mtime and served with `max-age`, and the page prefetches every
day's raster so playback never stalls. Index values 0–150 use the palette
embedded in the GeoTIFF; water/agriculture/urban specials and nodata are
transparent. The bundle also carries a per-fire `danger` series (mean index
over each tasking box) shown in the roster, tooltips and detail view.

## Page features

- Collapsible **workflow banner** at the top: a one-line summary of the demo
  pipeline (observe → process → predict → task) plus an SVG module-flow
  diagram from the demonstration deck. Hovering a module shows its role,
  deployment site and daily UTC slot; clicking jumps to the console view that
  shows its product (the fire-danger and planner/orbits nodes also switch on
  the corresponding map layer). The open/closed state is remembered per
  browser.
- Albers CONUS map with the day's tasking footprints; Arizona/New Mexico and
  Florida zoom insets show each footprint with its CYGNSS specular-point
  detections (filled = classified burned, hollow = unburned, opacity =
  classifier confidence).
- Toggleable **WFPI Day-1 fire-danger backdrop** (USGS palette, legend under
  the map) on the CONUS map and both insets, following the selected day; the
  roster/tooltips show each fire's box-mean danger index. Days without a
  product (e.g. 2026-07-10) say so in the legend row.
- **Soil moisture** card: two fixed retrieval sites (New Mexico, Texas
  panhandle), each with a day-following retrieved-field thumbnail (dry→wet
  colormap, non-retrieved pixels transparent) and a sparkline of the site's
  daily area-mean over the window; the sites are also marked on the CONUS map.
  Values are volumetric soil moisture (m³/m³); the domain/units are derived from
  the data. Days without a retrieval say "no retrieval this day".
- Toggleable **RawIF sweep**: a canvas overlay on the CONUS map animates the
  day's commanded RawIF captures as black dots (light ink in dark theme) at the
  actual specular-point locations — a clock sweeps the UTC day at 1440× (24 h ≙
  60 s), each dot lingering ~25 min of day-time with an age fade, and idle gaps
  between passes are skipped. A HUD pill shows the sweep clock and pass n/m
  plus a transport: pause/resume, previous/next pass, ±15 min rewind and
  fast-forward, a scrub slider over the day's passes (idle gaps compressed out
  of its range), and a speed cycle ¼×–4× on the base rate (paused state and
  speed carry across day changes for the session). Each satellite's newest
  visible dot is tagged with its CYGNSS NORAD id.
  Built by joining `planner/output`'s per-satellite RawIF command seconds
  against `orbits-actual`'s 2 Hz *actual* specular trajectories — all 4
  channels per satellite, first sample per second, deliberately unfiltered:
  a commanded second captures the whole receiver, so channels whose specular
  point falls outside CONUS (Gulf, Mexico, ocean) are shown as flown.
  ~170 MB CSV streamed once per day, cached gzipped by input mtimes
  (~1.2 s cold). Days with no plan+trajectory pair (only 2026-06-29 in the
  current data) say so in the HUD.
- **Fleet ops strip** (shows with the RawIF sweep, under the CONUS map):
  seven vertical storage bars — one per CYGNSS satellite — animate each
  buffer filling as RawIF observations are taken (1/60 of the 60-image
  buffer per obs) and draining during `DNL:` downlink windows (a full
  buffer empties in 20 min), simulated per day from an empty buffer at
  00:00 UTC. Three ground-station boxes (AUS · Australia, HI · Hawaii,
  CHI · Chile) light ember and name the transmitting satellite(s) while a
  downlink is in progress; the HUD shows "▼ downlink" at the same time.
  Downlink windows are folded into the sweep's pass timeline (they mostly
  fall between capture passes), so the scrub/prev/next controls traverse
  them too.
- Play / scrub the daily timeline (arrow keys, space).
- Click any fire (roster, box, marker, Gantt row, trend point) for a detail
  view: enlarged footprint with per-day or cumulative detections and a
  clickable burn-fraction strip.
- Burn-fraction trend chart per fire, and a persistence Gantt of the five-slot
  watchlist.
- Light/dark/auto theme toggle; live-sync indicator in the footer.
