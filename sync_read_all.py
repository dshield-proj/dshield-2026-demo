from rclone_r2 import sync_read_all

# Download: copy the read-only buckets → local directories
# (read-write buckets are left untouched; remotes are never modified)
sync_read_all()
