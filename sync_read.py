import argparse
from rclone_r2 import copy_read_bucket, sync_read_all

parser = argparse.ArgumentParser(description="Download R2 bucket data.")
parser.add_argument("bucket", nargs="?", help="Bucket name to download.")
parser.add_argument("--all", action="store_true", help="Download all buckets.")
args = parser.parse_args()

if bool(args.bucket) == args.all:
    parser.error("specify exactly one bucket name or --all")

# Download: copy remote bucket data → local directory. Remotes are never modified.
if args.all:
    sync_read_all()
else:
    copy_read_bucket(args.bucket)
