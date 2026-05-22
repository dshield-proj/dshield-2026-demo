from rclone_r2 import run_rclone, setup_rclone_remote, LOCAL_DIR, REMOTE_PATH

# Write rclone remote config if not already present
setup_rclone_remote()

# Sync local → remote
# Uploads new/changed files and deletes remote files removed locally
run_rclone("sync", LOCAL_DIR, REMOTE_PATH)
