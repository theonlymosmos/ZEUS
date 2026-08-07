# 11 — Rebuild from scratch (disaster recovery)

**Scenario:** the VM is gone — deleted, deallocated, or unrecoverable — and this
repository is all that survives.

This document takes you from an empty Azure subscription to a working honeynet.
It should take about 45 minutes, most of it waiting for Splunk to boot.

---

## 11.0 What this repo can and cannot restore

Be clear-eyed about this before you start.

### ✅ Fully restorable from this repo

| Thing | Source |
|---|---|
| Stack definition | `config/docker-compose.fixed.yml` |
| Cowrie image (honeyfs + userdb + fake filesystem) | `config/cowrie-custom/` — built, not vendored |
| Honeytoken file contents | `config/cowrie-custom/honeyfs/` |
| Credential policy | `config/cowrie-custom/userdb.txt` |
| Fake-filesystem metadata | **generated** by `build-fs.py` at image build |
| Splunk ingestion app | `config/splunk_apps/honeynet_inputs/` |
| Splunk dashboard | `config/splunk/dashboards/mousas_honeynet.xml` |
| Log bridge + systemd unit | `config/sync_logs.sh`, `config/systemd/` — *only needed if you deploy the un-fixed compose* |
| Azure NSG rules | [doc 02 §2.3](02-azure-and-host-setup.md) table |
| All operational procedure | docs 01–10 |

### ❌ NOT restorable — gone with the VM

| Thing | Why |
|---|---|
| **101 days of collected telemetry** (2,987 events, 318 attacker IPs) | Log data, never committed |
| **31 captured malware payloads** (~16 MB) | Deliberately gitignored — never commit malware |
| **19 TTY session recordings** | Same |
| **Dionaea bistreams** | Same |
| Splunk's indexed events | Derived from the above |

The *analysis* survives in [doc 10](10-observed-attack-data.md) — the numbers,
the credential lists, the command breakdown, the conclusions. The raw evidence
does not. If the VM still exists and you are reading this pre-emptively, **back
that data up now**: [doc 07 §7.8](07-operations-runbook.md).

> **The `lab_fs.pickle` decision.** The 1.2 MB fake-filesystem pickle is not
> committed. Python pickles execute arbitrary code on load, so a pickle in a
> public repo is a supply-chain hazard for anyone who clones it. Instead,
> `config/cowrie-custom/build-fs.py` regenerates it from the base filesystem
> shipped inside the Cowrie image, at `docker build` time. The generator derives
> Cowrie's internal type constants from the base pickle rather than hardcoding
> them, and verifies its own output before exiting non-zero on failure.

---

## 11.1 Provision the VM

```
Resource group: rg-honeynet-demo        (one dedicated group = one-command teardown)
OS:             Ubuntu Server 22.04 LTS or 24.04 LTS
Size:           Standard_B2s  (2 vCPU / 4 GB) minimum — Splunk is the constraint
Disk:           30 GB
Public IP:      STATIC  ← do this now; a dynamic IP changes on every deallocate
Auth:           SSH public key
Admin user:     azureuser
```

Inbound NSG rules — full rationale in [doc 02 §2.3](02-azure-and-host-setup.md):

| Port | Source | Note |
|---:|---|---|
| 22 | **your IP /32** | Never `Any`. Attackers actively brute-force `azureuser`. |
| 2222 | `Any` | Cowrie SSH trap |
| 2223 | `Any` | Cowrie Telnet trap |
| 21 | `Any` | Dionaea FTP |
| 2121 | `Any` | Dionaea FTP fallback |
| 8000 | *(omit)* | Not needed — `docker-compose.fixed.yml` binds Splunk to loopback; use an SSH tunnel |

## 11.2 Prepare the host

```bash
ssh -i <key>.pem azureuser@<VM_PUBLIC_IP>

sudo apt update
sudo apt install -y docker.io docker-compose-plugin git jq curl lftp
sudo systemctl enable --now docker
sudo usermod -aG docker $USER    # log out and back in, or keep using sudo
```

Confirm you have **Compose V2** — the legacy v1 script fails with
`KeyError: 'ContainerConfig'` against modern Docker:

```bash
sudo docker compose version      # note the SPACE, not docker-compose
```

## 11.3 Lay down the configuration

```bash
git clone https://github.com/theonlymosmos/ZEUS.git ~/zeus
mkdir -p ~/honeynet
cp -r ~/zeus/config/. ~/honeynet/
cd ~/honeynet

# Use the corrected stack, not the as-deployed record
mv docker-compose.yml docker-compose.as-deployed.yml.bak
mv docker-compose.fixed.yml docker-compose.yml

# Log directories the compose file binds
mkdir -p cowrie-logs dionaea-logs

# Splunk password
cp .env.example .env
nano .env                        # set a real SPLUNK_PASSWORD (8+ chars)
chmod 600 .env
```

## 11.4 Build and launch

```bash
cd ~/honeynet
sudo docker compose build --no-cache cowrie
```

Watch for the filesystem generator's output — this is the step that replaces the
uncommitted pickle:

```
base pickle: /cowrie/cowrie-git/src/cowrie/data/fs.pickle
derived types: T_DIR=... T_FILE=...
registering honeytokens:
  + dir  phil/
  + file /home/phil/Project_Zeus_Master_DB_Backup.sql  (4096 bytes)
  + file /home/phil/notes.txt  (1024 bytes)
  + file /home/phil/deploy.sh  (2048 bytes)
  + file /home/phil/.bash_history  (2048 bytes)
  + file /var/log/auth.log  (2048 bytes)
OK: wrote and verified /cowrie/cowrie-git/share/cowrie/lab_fs.pickle
```

If the build fails at `find_base_pickle`, the upstream image has moved
`fs.pickle`. Locate it and add the path to the candidate list in `build-fs.py`:

```bash
sudo docker run --rm --entrypoint /cowrie/cowrie-env/bin/python3 \
  cowrie/cowrie:latest -c \
  "import pathlib;[print(p) for p in pathlib.Path('/cowrie').rglob('fs.pickle')]"
```

Then start everything:

```bash
sudo docker compose up -d
watch -n5 'sudo docker ps --format "table {{.Names}}\t{{.Status}}"'
```

Wait for `splunk_dashboard` to reach `(healthy)` — 2–5 minutes on a B2s.

## 11.5 Restore the dashboard

Splunk's index starts empty; the dashboard has to be re-imported once.

```bash
# Reach Splunk over an SSH tunnel (it is bound to loopback)
# Run this on YOUR machine, not the VM:
ssh -i <key>.pem -L 8000:localhost:8000 azureuser@<VM_PUBLIC_IP>
```

Then browse to `http://localhost:8000`, log in as `admin`, and:

1. **Search & Reporting** → **Dashboards** → **Create New Dashboard**
2. Title: `mousas_honeynet` (this becomes the URL slug — keep it exact)
3. Choose **Classic Dashboards** → **Create**
4. **Edit** → **Source**
5. Paste the contents of `config/splunk/dashboards/mousas_honeynet.xml` → **Save**

Better: ship it inside the app so it survives the next rebuild automatically.

```bash
mkdir -p ~/honeynet/splunk_apps/honeynet_inputs/default/data/ui/views
cp ~/zeus/config/splunk/dashboards/mousas_honeynet.xml \
   ~/honeynet/splunk_apps/honeynet_inputs/default/data/ui/views/
sudo docker restart splunk_dashboard
```

It then appears at `/en-US/app/honeynet_inputs/mousas_honeynet` with no manual
import. Set `is_visible = 1` in `default/app.conf` to give it a menu entry.

## 11.6 Verify the rebuild

Work through all six. Do not declare victory before the honeytoken check —
that is the one that was broken in the original deployment.

```bash
# 1. All three containers up
sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

# 2. Cowrie listening on both protocols
sudo docker logs cowrie_honeypot 2>&1 | grep 'Ready to accept'
#    expect: SSH connections  AND  Telnet connections

# 3. userdb.txt ACTUALLY loaded — this must return 0
sudo docker logs cowrie_honeypot 2>&1 | grep -c 'Could not read etc/userdb.txt'

# 4. honeyfs present where Cowrie reads it — must list the phil files
sudo docker cp cowrie_honeypot:/cowrie/cowrie-git/honeyfs - | tar -tv | grep phil

# 5. Splunk serving (via the tunnel, or on the VM)
curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://localhost:8000   # expect 303

# 6. Logs flowing directly — no bridge needed now
ls -lh ~/honeynet/cowrie-logs/cowrie.json
```

**The end-to-end test.** From another machine:

```bash
ssh -o StrictHostKeyChecking=no -p 2222 root@<VM_PUBLIC_IP>
# password: anything except "root", "123456", or something matching /honeypot/i
#   (userdb.txt is `root:x:*`, but Cowrie's own defaults deny those three)

cd /home/phil
ls -la                                    # honeytokens must be VISIBLE
cat Project_Zeus_Master_DB_Backup.sql     # and must RETURN CONTENT
exit
```

That `cat` returning content is the proof F-01 is fixed. In the original
deployment it returned empty.

Then confirm it reached Splunk:

```spl
index=main sourcetype=cowrie:json earliest=-15m | head 20
```

## 11.7 Post-rebuild cleanup

```bash
# The log bridge is obsolete once F-01 is fixed — Cowrie writes to the host
# directly. Do NOT install honeynet-sync.service, and remove the cron entry
# if you ever added it.
crontab -l    # should contain no honeynet @reboot line

# Sane permissions (F-06) — do NOT chmod -R 777
chmod 755 ~/honeynet
chmod 600 ~/honeynet/.env
sudo chown -R azureuser:docker ~/honeynet/cowrie-logs ~/honeynet/dionaea-logs
chmod 775 ~/honeynet/cowrie-logs ~/honeynet/dionaea-logs
```

## 11.8 Fix the corrupted honeytoken

The committed `Project_Zeus_Master_DB_Backup.sql` is damaged — a shell redirect
leaked into it during the original build (see [doc 03 §3.6](03-cowrie-config.md)).
Restore it with a **quoted** heredoc; the quotes around `'EOF'` are what stop
bash mangling the `!` and the `2>`:

```bash
cat > ~/honeynet/cowrie-custom/honeyfs/home/phil/Project_Zeus_Master_DB_Backup.sql <<'EOF'
-- Master DB Backup - Project Zeus
-- Generated by backup-db.sh at 02:00 UTC
-- WARNING: rotate these credentials after migration.

CREATE DATABASE zeus_prod;
USE zeus_prod;

INSERT INTO users (username, password, role) VALUES
('admin', 'Zeus_Admin_Access_99!', 'superadmin'),
('backup_svc', 'BackupSvc_2026_DoNotShare!', 'service'),
('reporting', 'ReadOnly_Report_7781!', 'readonly');
EOF

cd ~/honeynet
sudo docker compose build --no-cache cowrie
sudo docker compose up -d cowrie
```

> While you are here: the honeytoken filenames and fake credentials are public
> in this repo. If you want the deception to be genuinely effective against
> someone who has read it, change them. Keep the *shape* — a plausible dump, a
> notes file, a deploy script with an env block, a suggestive `.bash_history` —
> and change the names and values.

## 11.9 Start collecting again

Nothing more to do. The honeypot is exposed; traffic arrives on its own. The
original deployment logged its first attack **4 minutes** after coming online,
and averaged 5.6 sessions and ~3 new source IPs per day without being advertised
anywhere.

**Back up from day one this time:**

```bash
# Weekly snapshot of the collected evidence
V=$(sudo docker volume inspect honeynet_cowrie-var --format '{{.Mountpoint}}')
sudo tar czf ~/honeynet-data-$(date +%F).tar.gz \
  -C "$V" . \
  -C /home/azureuser/honeynet cowrie-logs dionaea-logs
```

Pull it off the VM regularly. The configuration is reproducible from git in
45 minutes; the collected telemetry is not reproducible at all.

---

Back to the [README](../README.md).
