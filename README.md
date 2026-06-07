# dshield-2026-demo


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

A `cloudflare_r2.json` credentials will be supplied to you seperately.


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

5. The data of the respective modules will sit in the below named folders, and the directory stucture will look like as shown below.

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
├── setup.py
├── sync_rw.py
└── sync_read_all.py
```




### Usage during the demo 

1. Refresh the latest data from all buckets (read-only and read-write).

```bash
python sync_read.py --all
```

To refresh only one bucket:

```bash
python sync_read.py burned-area
```

2. Open the configuration file corresponding to the day from `dshield-demo-configuration/` bucket. E.g. on 2026-04-24 refer to `dshield_demo_config_20260424.json`. A new file is produced for each day and posted in the bucket. It specifies the output path each module should write to:

```json
dshield_demo_config_20260424.json
---------------------------------
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
python sync_rw.py fire-arrival
```

To upload all read-write buckets:

```bash
python sync_rw.py --all
```
By default files deleted locally are **not** deleted in remote.

To also delete files from the remote bucket that no longer exist locally, pass `--delete`:

```bash
python sync_rw.py fire-arrival --delete
```
