# dshield-2026-demo


## Setup for syncing local data to the remote buckets

### Design

- Each component feature has its own dedicated bucket.
- Each bucket has a single designated writer. Only that person holds write permissions; everyone else has read-only access.
  - Cloudflare R2 does not support folder-level access control within a bucket, so per-bucket token scoping is the finest granularity available.
  - Scoping writes to one person per bucket prevents accidental deletions: if a user deletes local files and runs a sync, only their own bucket is affected.
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

### 2. Add files to your project

Copy these three files into the folder where the results of your process lives:

- `rclone_r2.py`
- `sync_rw.py`
- `sync_ro.py`
- `cloudflare_r2.json` — obtain this from the bucket owner; **never commit it** (it is gitignored)

### 3. Configure credentials

The `cloudflare_r2.json` credentials will be supplied to you seperately.

## Usage

### Preliminary setup

1. Create a local directory named `dshield-2026-demo` to hold the data for all dshield modules.

2. Copy `rclone_r2.py`, `sync_rw.py`, and `sync_ro.py` from this repo into that directory, then place your configured `cloudflare_r2.json` there as well.

3. The data of the respective modules will sit in the below named folders, and the directory stucture will look like as shown below.

```
dshield-2026-demo/
├── dshield-demo-configuration/   # daily config files (synced from remote)
├── active-fire-priority/
├── burned_area/
├── fire-arrival/
├── fire-danger/
├── fire-severity-predictor/
├── orbits/
├── planner/
├── pre-fire-priority/
├── soil-moisture/
├── cloudflare_r2.json
├── rclone_r2.py
├── sync_rw.py
└── sync_ro.py
```

### Usage during the demo 

1. Download the latest data from all read-only buckets, including the daily configuration file from `dshield-demo-configuration/`:

```bash
python sync_ro.py
```

2. Open the configuration file correspoding to the day from `dshield-demo-configuration/` (e.g. on 2026-04-24 refer to `dshield_demo_config_20260424.json`). A new file is produced each day and shared across all users. It specifies the output path each module should write to:

```json
{
  "scenario_info": {
    "scenario_id": "scenario1",
    "sim_execution_date": "20260422",
    "today": "20260422",
    "sim_start_date": "20260422",
    "sim_day_number": 0
  },
  "outputs": {
    "soil_moisture":        "/soil-moisture/output/20260419/",
    "burned_area":          "/burned-area/output/20260419/",
    "fire_arrival":         "/fire-arrival/output/20260419/",
    "fire_danger":          "/fire-danger/output/20260422/",
    "pre_fire_priority":    "/pre-fire-priority/output/20260424/",
    "active_fire_priority": "/active-fire-priority/output/20260424/",
    "orbits":               "/orbits/output/20260424/",
    "planner":              "/planner/output/20260424/"
  }
}
```

3. Run your module (e.g. `python orbits.py`). It should read the config file and write its outputs to the path listed under `outputs` for your component (e.g. `orbits/output/20260424/` relative to `dshield-2026-demo/`).

4. Upload your results to the remote bucket:

```bash
python sync_rw.py
```

Buckets you have **write access** to are updated with your local changes. Buckets you have **read-only access** to are updated with the latest remote data.

- **Read-write buckets** — runs `rclone sync local_dir → remote:bucket` (uploads changes; files deleted locally are also removed from the bucket).
- **Read-only buckets** — runs `rclone copy remote:bucket → local_dir` (downloads only; the remote is never modified). Any local edits to files that exist in the remote bucket will be overwritten on the next sync. Local files that have no counterpart in the remote are left untouched.

