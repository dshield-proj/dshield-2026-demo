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
    required = {"rclone_remote", "bucket_name", "local_dir", "account_id", "access_key_id", "secret_access_key"}
    missing = required - cfg.keys()
    if missing:
        print(f"Missing keys in config: {missing}", file=sys.stderr)
        sys.exit(1)
    return cfg


_cfg = load_config()

RCLONE_REMOTE = _cfg["rclone_remote"]
BUCKET_NAME = _cfg["bucket_name"]
LOCAL_DIR = _cfg["local_dir"]

# Full remote path
REMOTE_PATH = f"{RCLONE_REMOTE}:{BUCKET_NAME}"


def setup_rclone_remote():
    """Configure the rclone remote from cloudflare_r2.json.

    Uses configparser to read and write rclone.conf so that any other remotes
    already present (e.g. Google Drive, AWS S3) are preserved. Only the target
    remote section is added or updated; the rest of the file is left untouched.
    If the stored credentials already match the config, the file is not rewritten.
    """
    import configparser

    rclone_conf = Path.home() / ".config" / "rclone" / "rclone.conf"
    rclone_conf.parent.mkdir(parents=True, exist_ok=True)

    desired = {
        "type": "s3",
        "provider": "Cloudflare",
        "access_key_id": _cfg["access_key_id"],
        "secret_access_key": _cfg["secret_access_key"],
        "endpoint": f"https://{_cfg['account_id']}.r2.cloudflarestorage.com",
    }

    parser = configparser.ConfigParser()
    if rclone_conf.exists():
        parser.read(rclone_conf)

    if RCLONE_REMOTE in parser and dict(parser[RCLONE_REMOTE]) == desired:
        print(f"rclone remote '{RCLONE_REMOTE}' is up to date, skipping.")
        return

    action = "Updated" if RCLONE_REMOTE in parser else "Created"
    parser[RCLONE_REMOTE] = desired

    with rclone_conf.open("w") as f:
        parser.write(f)
    rclone_conf.chmod(0o600)
    print(f"{action} rclone remote '{RCLONE_REMOTE}' in {rclone_conf}")


def run_rclone(command, *args):
    cmd = ["rclone", command, *args, "--progress", "--verbose"]
    print(f"Running: {' '.join(cmd)}\n")
    result = subprocess.run(cmd, text=True)
    if result.returncode != 0:
        print(f"rclone exited with code {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)

