from rclone_r2 import sync_read_all

# Download: copy all buckets (read-only and read-write) → local directories
# (remotes are never modified)
sync_read_all()
