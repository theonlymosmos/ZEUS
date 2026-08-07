#!/bin/bash
export DB_HOST=10.10.8.15
export DB_NAME=zeus_prod
export DB_USER=backup_svc
export DB_PASS=BackupSvc_2026_DoNotShare!

echo "Deploying Project Zeus API..."
# rsync -avz ./build/ zeus-api:/srv/zeus/
# systemctl restart zeus-api
