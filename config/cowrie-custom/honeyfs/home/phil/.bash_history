ssh admin@10.10.8.15
mysql -u backup_svc -p zeus_prod
cat Project_Zeus_Master_DB_Backup.sql | grep password
scp Project_Zeus_Master_DB_Backup.sql backup@10.10.8.20:/mnt/backups/
history -c
