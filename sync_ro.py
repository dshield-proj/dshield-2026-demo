from rclone_r2 import setup_rclone_remotes, sync_ro_buckets

# Write rclone remote config if not already present
setup_rclone_remotes()

# Download: copy read-only buckets → local directories
# (remote is never modified)
sync_ro_buckets()
