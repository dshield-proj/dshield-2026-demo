import argparse
from rclone_r2 import copy_rw_bucket, copy_rw_buckets, sync_rw_bucket, sync_rw_buckets

parser = argparse.ArgumentParser(description="Upload read-write R2 bucket data.")
parser.add_argument("bucket", nargs="?", help="Bucket name to upload.")
parser.add_argument("--all", action="store_true", help="Upload all read-write buckets.")
parser.add_argument(
    "--delete",
    action="store_true",
    help="Mirror local deletions to the remote bucket (uses rclone sync instead of copy).",
)
args = parser.parse_args()

if bool(args.bucket) == args.all:
    parser.error("specify exactly one bucket name or --all")

# Upload: copy local directories → read-write buckets.
# Pass --delete to also remove files from the bucket that were deleted locally.
if args.all:
    if args.delete:
        sync_rw_buckets()
    else:
        copy_rw_buckets()
else:
    if args.delete:
        sync_rw_bucket(args.bucket)
    else:
        copy_rw_bucket(args.bucket)
