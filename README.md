# dshield-2026-demo

Analysis and visualization of the DShield 2026 wildfire-monitoring demo: a
burned-area tasking campaign (2026-06-29 → 2026-07-10) in which five
prioritized fires per day were observed by the CYGNSS fleet, plus the
fire-danger (WFPI Day-1), soil-moisture, and RawIF command-plan layers. The
main deliverable is the **fire console**, an interactive web dashboard that
reads the module outputs live from disk.

## Fire console

A self-hosted console (stdlib-only Python, no dependencies) showing daily
tasking footprints on a CONUS map, CYGNSS burned/unburned detections, the
WFPI fire-danger backdrop, soil-moisture retrievals, and an animated RawIF
sweep of the fleet's commanded captures with per-satellite storage and
ground-station downlinks.

### Run

```bash
python3 fire-console/server.py     # → http://localhost:8000
```

It reads the sibling data folders (`dshield-demo-configuration/`,
`burned-area/`, `fire-danger/`, `soil-moisture/`, `planner/`,
`orbits-actual/`) live — new day folders appear on the page within ~5 s.

If the server runs on a remote machine (e.g. EC2), forward the port and
browse from your laptop:

```bash
ssh -L 8000:localhost:8000 ubuntu@<host> -i <your-key.pem>
```

then open <http://localhost:8000>. VS Code Remote-SSH users can instead
forward port 8000 from the **Ports** panel.

See [fire-console/README.md](fire-console/README.md) for configuration
(ports, data paths, optional auth token), the JSON API, and the full feature
list.

## Setup for syncing local data to the remote buckets

### Design

- Each component feature has its own dedicated bucket.
- Each bucket has a single designated writer. Only that person holds write permissions; everyone else has read-only access.
  - Cloudflare R2 does not support folder-level access control within a bucket, so per-bucket token scoping is the finest granularity available.
  - Scoping writes to one person per bucket limits the effect of accidental deletions: if a user deletes local files and runs a sync, only their own bucket is affected.
- All users have read access to every bucket.

### 1. Install rclone

**Ubuntu:**
```bash
sudo -v ; curl https://rclone.org/install.sh | sudo bash
rclone version   # verify
```

**macOS:**
```bash
brew install rclone
# or, without Homebrew:
sudo -v ; curl https://rclone.org/install.sh | sudo bash
which rclone     # verify
```

### 2. Configure credentials

A `cloudflare_r2.json` credentials file will be supplied to you separately
(its expected format is shown in `cloudflare_r2_template.json` in this repo).
It lists, for each of the two access tokens (read-write and read-only), the
buckets to sync and the local directory each one maps to.


## Usage

### Preliminary setup

1. Create a local directory named `dshield-2026-demo` to hold the data for all dshield modules.

2. Copy `rclone_r2.py`, `sync_rw.py`, `sync_read_all.py`, and `setup.py` from this repo into that directory, then place your configured `cloudflare_r2.json` there as well.

3. Configure the rclone remotes (only needed once per machine):

```bash
python setup.py
```
(If the credentials change, then `setup.py` will need to be re-run.)

4. Read the existing data from remote to your local drive (within the `dshield-2026-demo` directory)

```bash
python sync_read_all.py
```

This downloads the buckets listed under `read_only` in your
`cloudflare_r2.json`; your own read-write bucket is not touched (it is seeded
from the remote automatically the first time you run `sync_rw.py`, if its
local directory doesn't exist yet).

5. The data of the respective modules will sit in the below named folders, and the directory structure will look as shown below.

```
dshield-2026-demo/
├── dshield-demo-configuration/ # config files, proxy active fire priority CSVs
├── burned-area/ # burned area detections
├── fire-arrival/ # fire arrival data
├── fire-danger/ # fire danger forecast data
├── orbits/ # orbit (EOSim) predictions
├── planner/ # planner output -> planned satellite tasks
├── pre-fire-priority/ # pre fire priority CSVs
├── soil-moisture/ # soil moisture data
├── orbits-actual/ # actual specular points observations from CYGNSS L1
├── fire-console/ # script to visualize results
├── helper_scripts/ # some helper scripts
├── cloudflare_r2.json
├── rclone_r2.py
├── setup.py
├── sync_rw.py
└── sync_read_all.py
```


### Usage during the demo 

1. Refresh the latest data from the read-only buckets (your read-write
buckets are left untouched, so local outputs are never overwritten).

```bash
python sync_read_all.py
```

2. Open the configuration file corresponding to the day from the
`dshield-demo-configuration/` bucket: the daily master config
`dshield-demo-config-YYYYMMDD.json` (e.g. `dshield-demo-config-20260702.json`)
sits at the bucket root and lists, under `outputs`, the path each component
should write to. Depending on the component, an additional per-day
configuration file may apply, under `soil-moisture-config/`,
`burned-area-config/`, or `planner_config/`.

The `dshield-demo-configuration/` bucket also contains the active-fire Target
Value CSVs (inputs to the planner) in the `active-fire-priority-proxy/` folder.

3. Run your module (e.g. `python orbits.py`). It should read the config file and write its outputs to the path listed under `outputs` for your component (e.g. the 2026-07-02 config sends `orbits` output to `orbits/output/20260703/`, relative to `dshield-2026-demo/`).

4. Upload your results to the remote bucket:

```bash
python sync_rw.py
```
By default files deleted locally are **not** deleted in remote.

To also delete files from the remote bucket that no longer exist locally, pass `--delete`:

```bash
python sync_rw.py --delete
```