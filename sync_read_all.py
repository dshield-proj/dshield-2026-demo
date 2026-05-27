from rclone_r2 import setup_rclone_remotes, sync_read_all

# Write rclone remote config if not already present
setup_rclone_remotes()

# Download: copy all buckets (read-only and read-write) → local directories
# (remotes are never modified)
sync_read_all()
