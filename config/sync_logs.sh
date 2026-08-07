#!/bin/bash
# 1. Hunt for the ghost volume
NEW_PATH=$(sudo docker inspect cowrie_honeypot --format='{{range .Mounts}}{{if eq .Destination "/cowrie/cowrie-git/var"}}{{.Source}}{{end}}{{end}}')

# 2. Check if the path exists
if [ -z "$NEW_PATH" ]; then
    echo "Container not ready. Systemd will retry in 10s..."
    exit 1
fi

# 3. Start the stream (No pkill, no backgrounding)
# We use 'exec' so the tail process replaces the script process
exec sudo tail -F $NEW_PATH/log/cowrie/cowrie.json >> /home/azureuser/honeynet/cowrie-logs/cowrie.json
