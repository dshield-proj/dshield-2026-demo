from rclone_r2 import setup_rclone_remotes

# Configure rclone remotes for Cloudflare R2 (read-write and read-only).
# Run this once before using sync_rw.py or sync_read_all.py.
setup_rclone_remotes()
