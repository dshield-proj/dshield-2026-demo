from rclone_r2 import setup_rclone_remotes, sync_rw_buckets

# Write rclone remote config if not already present
setup_rclone_remotes()

# Upload: sync local directories → read-write buckets
# (files deleted locally are also removed from the bucket)
sync_rw_buckets()
