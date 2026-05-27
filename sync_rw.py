import argparse
from rclone_r2 import copy_rw_buckets, sync_rw_buckets

parser = argparse.ArgumentParser(description="Upload local directories to read-write R2 buckets.")
parser.add_argument(
    "--delete",
    action="store_true",
    help="Mirror local deletions to the remote bucket (uses rclone sync instead of copy).",
)
args = parser.parse_args()

# Upload: copy local directories → read-write buckets
# Pass --delete to also remove files from the bucket that were deleted locally
if args.delete:
    sync_rw_buckets()
else:
    copy_rw_buckets()
