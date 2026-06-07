"""
This module provides functions to set up rclone remotes for
Cloudflare R2 and to sync buckets according to the configuration
in cloudflare_r2.json.

`cloudflare_r2.json` uses two sets of API tokens — one for buckets with read-write access,
one for read-only buckets:

```jsonc
{
    "account_id": "...",          // shared Cloudflare account ID

    "read_write": {
        "rclone_remote": "r2-rw", // name for the rclone remote
        "access_key_id": "...",
        "secret_access_key": "...",
        "buckets": [
            { "bucket_name": "my-bucket-1", "local_dir": "/data/bucket1" },
            ...                   // one entry per read-write bucket
        ]
    },

    "read_only": {
        "rclone_remote": "r2-ro",
        "access_key_id": "...",
        "secret_access_key": "...",
        "buckets": [
            { "bucket_name": "my-bucket-4", "local_dir": "/data/bucket4" },
            ...                   // one entry per read-only bucket
        ]
    }
}
```

"""
import configparser
import json
import subprocess
import sys
from pathlib import Path


def load_config(config_path="cloudflare_r2.json"):
    path = Path(config_path)
    if not path.exists():
        print(f"Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)
    with path.open() as f:
        cfg = json.load(f)

    # Validate top-level keys
    for key in ("account_id", "read_write", "read_only"):
        if key not in cfg:
            print(f"Missing top-level key in config: '{key}'", file=sys.stderr)
            sys.exit(1)

    # Validate each token-set section
    for section in ("read_write", "read_only"):
        sec = cfg[section]
        for key in ("rclone_remote", "access_key_id", "secret_access_key", "buckets"):
            if key not in sec:
                print(f"Missing key '{key}' in config['{section}']", file=sys.stderr)
                sys.exit(1)
        for i, bucket in enumerate(sec["buckets"]):
            for key in ("bucket_name", "local_dir"):
                if key not in bucket:
                    print(
                        f"Missing key '{key}' in config['{section}']['buckets'][{i}]",
                        file=sys.stderr,
                    )
                    sys.exit(1)

    return cfg


_cfg = load_config()


def _setup_remote(parser, remote_name, account_id, access_key_id, secret_access_key):
    """Add or update a single rclone remote section. Returns True if a write is needed."""
    desired = {
        "type": "s3",
        "provider": "Cloudflare",
        "access_key_id": access_key_id,
        "secret_access_key": secret_access_key,
        "endpoint": f"https://{account_id}.r2.cloudflarestorage.com",
    }
    if remote_name in parser and dict(parser[remote_name]) == desired:
        print(f"rclone remote '{remote_name}' is up to date, skipping.")
        return False
    action = "Updated" if remote_name in parser else "Created"
    parser[remote_name] = desired
    print(f"{action} rclone remote '{remote_name}'.")
    return True


def setup_rclone_remotes():
    """Configure both rclone remotes (read-write and read-only) from cloudflare_r2.json.

    Uses configparser so that unrelated remotes already present in rclone.conf
    are preserved. Only the two managed sections are added or updated.
    """
    rclone_conf = Path.home() / ".config" / "rclone" / "rclone.conf"
    rclone_conf.parent.mkdir(parents=True, exist_ok=True)

    parser = configparser.ConfigParser()
    if rclone_conf.exists():
        parser.read(rclone_conf)

    account_id = _cfg["account_id"]
    changed = False
    for section in ("read_write", "read_only"):
        sec = _cfg[section]
        changed |= _setup_remote(
            parser,
            sec["rclone_remote"],
            account_id,
            sec["access_key_id"],
            sec["secret_access_key"],
        )

    if changed:
        with rclone_conf.open("w") as f:
            parser.write(f)
        rclone_conf.chmod(0o600)
        print(f"rclone.conf written to {rclone_conf}")


def run_rclone(command, *args):
    cmd = ["rclone", command, *args, "--exclude", "/.**", "--exclude", "**/.**", "--progress", "--verbose"]
    print(f"Running: {' '.join(cmd)}\n")
    result = subprocess.run(cmd, text=True)
    if result.returncode != 0:
        print(f"rclone exited with code {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)


def _find_bucket(section, bucket_name):
    """Return one configured bucket by bucket_name from the requested section."""
    sec = _cfg[section]
    matches = [bucket for bucket in sec["buckets"] if bucket["bucket_name"] == bucket_name]
    if not matches:
        print(f"Bucket '{bucket_name}' not found in config['{section}']['buckets']", file=sys.stderr)
        sys.exit(1)
    if len(matches) > 1:
        print(f"Bucket '{bucket_name}' appears more than once in config['{section}']['buckets']", file=sys.stderr)
        sys.exit(1)
    return matches[0]


def _copy_read_bucket(section, bucket_name):
    """Download one configured bucket from remote to local with rclone copy."""
    sec = _cfg[section]
    bucket = _find_bucket(section, bucket_name)
    remote = sec["rclone_remote"]
    label = "read-only " if section == "read_only" else "read-write"
    local = bucket["local_dir"]
    remote_path = f"{remote}:{bucket['bucket_name']}"
    Path(local).mkdir(parents=True, exist_ok=True)
    print(f"\n[{label}]  {remote_path}  →  {local}")
    run_rclone("copy", remote_path, local)


def copy_read_bucket(bucket_name):
    """Download one bucket by name from remote to local.

    Read-only and read-write buckets are both downloaded with rclone copy.
    """
    sections = [
        section
        for section in ("read_only", "read_write")
        if any(bucket["bucket_name"] == bucket_name for bucket in _cfg[section]["buckets"])
    ]
    if len(sections) == 1:
        _copy_read_bucket(sections[0], bucket_name)
        return
    if len(sections) > 1:
        print(f"Bucket '{bucket_name}' appears in both read-only and read-write config", file=sys.stderr)
        sys.exit(1)
    print(f"Bucket '{bucket_name}' not found in read-only or read-write config", file=sys.stderr)
    sys.exit(1)


def _copy_rw_bucket(bucket_name, rclone_command):
    """Upload one configured read-write bucket from local to remote."""
    sec = _cfg["read_write"]
    remote = sec["rclone_remote"]
    bucket = _find_bucket("read_write", bucket_name)
    local = bucket["local_dir"]
    remote_path = f"{remote}:{bucket['bucket_name']}"
    if not Path(local).exists():
        print(
            f"[read-write] Local directory not found — creating and seeding from remote: {local}"
        )
        Path(local).mkdir(parents=True, exist_ok=True)
        run_rclone("copy", remote_path, local)
    print(f"\n[read-write] {local}  →  {remote_path}")
    run_rclone(rclone_command, local, remote_path)


def copy_rw_bucket(bucket_name):
    """Upload one read-write bucket using rclone copy."""
    _copy_rw_bucket(bucket_name, "copy")


def sync_rw_bucket(bucket_name):
    """Upload one read-write bucket using rclone sync."""
    _copy_rw_bucket(bucket_name, "sync")


def _rw_buckets_loop(rclone_command):
    """Shared iteration logic for read-write bucket operations."""
    sec = _cfg["read_write"]
    remote = sec["rclone_remote"]
    for bucket in sec["buckets"]:
        local = bucket["local_dir"]
        remote_path = f"{remote}:{bucket['bucket_name']}"
        if not Path(local).exists():
            print(
                f"[read-write] Local directory not found — creating and seeding from remote: {local}"
            )
            Path(local).mkdir(parents=True, exist_ok=True)
            run_rclone("copy", remote_path, local)
        print(f"\n[read-write] {local}  →  {remote_path}")
        run_rclone(rclone_command, local, remote_path)


def copy_rw_buckets():
    """Upload each read-write bucket: local directory → remote bucket.

    Uses 'rclone copy', so files deleted locally are NOT removed from the bucket.

    If the local directory does not exist it is created and seeded from the remote
    using 'rclone copy' before copying. This preserves any existing remote
    content on first-time setup.
    """
    _rw_buckets_loop("copy")


def sync_rw_buckets():
    """Sync each read-write bucket: local directory → remote bucket.

    Uses 'rclone sync', so files deleted locally are also removed from the bucket.
    For non-destructive uploads use copy_rw_buckets() instead.

    If the local directory does not exist it is created and seeded from the remote
    using 'rclone copy' before the sync runs. This preserves any existing remote
    content on first-time setup and avoids wiping the bucket with an empty source.
    """
    _rw_buckets_loop("sync")


def sync_read_all():
    """Download all buckets (read-only and read-write): remote → local directory.

    Uses 'rclone copy' so remotes are never modified. Local directories are
    created automatically if they do not exist.

    Note on local edits: any local file that also exists in the remote bucket
    will be overwritten if its content differs from the remote version. Local
    files that have no counterpart in the remote are left untouched, since
    'rclone copy' never deletes files from the destination.
    """
    for section in ("read_only", "read_write"):
        sec = _cfg[section]
        remote = sec["rclone_remote"]
        label = "read-only " if section == "read_only" else "read-write"
        for bucket in sec["buckets"]:
            local = bucket["local_dir"]
            remote_path = f"{remote}:{bucket['bucket_name']}"
            Path(local).mkdir(parents=True, exist_ok=True)
            print(f"\n[{label}]  {remote_path}  →  {local}")
            run_rclone("copy", remote_path, local)
