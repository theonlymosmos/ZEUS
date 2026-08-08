# 07 — Operations runbook

Everything needed to connect, check health, restart, and troubleshoot. The
commands here are the ones actually used against this deployment — recovered
from the host's shell history — not generic advice.

## 7.1 Connecting to the VM

### From MobaXterm (Windows)

MobaXterm is the client used to operate this deployment.

**Create the session once:**

1. **Session** → **SSH**
2. Remote host: `<VM_PUBLIC_IP>`
3. ☑ Specify username: `azureuser`
4. Port: `22`
5. **Advanced SSH settings** → ☑ **Use private key** → browse to
   `mousahoneypot_key.pem`
6. Save as `mousahoneypot`

**If MobaXterm rejects the key** with a permissions complaint, it is reading the
`.pem` from a Windows path with inherited ACLs. Either move the key inside
MobaXterm's home (`Settings → Configuration → General → Persistent home
directory`), or fix the ACL:

```powershell
icacls "D:\path\to\mousahoneypot_key.pem" /inheritance:r
icacls "D:\path\to\mousahoneypot_key.pem" /grant:r "$($env:USERNAME):(R)"
```

MobaXterm's left pane also gives you SFTP into `/home/azureuser` on the same
connection — the easiest way to pull `cowrie.json` down for offline analysis.

### From any OpenSSH client

```bash
chmod 600 mousahoneypot_key.pem                       # Linux/macOS/Git Bash
ssh -i mousahoneypot_key.pem azureuser@<VM_PUBLIC_IP>
```

```powershell
# Windows PowerShell
icacls .\mousahoneypot_key.pem /inheritance:r
icacls .\mousahoneypot_key.pem /grant:r "$($env:USERNAME):(R)"
ssh -i .\mousahoneypot_key.pem azureuser@<VM_PUBLIC_IP>
```

> If the connection times out, the public IP has almost certainly changed. See
> [doc 02 §2.1](02-azure-and-host-setup.md).

## 7.2 The 30-second health check

Run this first, every time. It answers "is everything up?" in one screen.

```bash
echo "=== CONTAINERS ==="
sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

echo "=== LOG BRIDGE ==="
sudo systemctl is-active honeynet-sync.service
ps aux | grep '[t]ail -F' | grep -c cowrie          # want exactly 1

echo "=== LISTENERS ==="
sudo ss -tulpn | grep -E ':(21|2121|2222|2223|8000)\b'

echo "=== SPLUNK WEB ==="
curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://localhost:8000

echo "=== LOG FLOW ==="
ls -lh ~/honeynet/cowrie-logs/cowrie.json
```

### Healthy output

```
=== CONTAINERS ===
NAMES              STATUS                 PORTS
cowrie_honeypot    Up 6 hours             0.0.0.0:2222-2223->2222-2223/tcp
splunk_dashboard   Up 6 hours (healthy)   0.0.0.0:8000->8000/tcp, ...
dionaea_honeypot   Up 6 hours             0.0.0.0:21->21/tcp, 0.0.0.0:2121->21/tcp

=== LOG BRIDGE ===
active
1

=== LISTENERS ===
tcp LISTEN 0 4096 0.0.0.0:8000  ... docker-proxy
tcp LISTEN 0 4096 0.0.0.0:2121  ... docker-proxy
tcp LISTEN 0 4096 0.0.0.0:2223  ... docker-proxy
tcp LISTEN 0 4096 0.0.0.0:2222  ... docker-proxy
tcp LISTEN 0 4096 0.0.0.0:21    ... docker-proxy

=== SPLUNK WEB ===
HTTP 303

=== LOG FLOW ===
-rwxrwxrwx 1 azureuser azureuser 2.6M Aug  7 09:14 cowrie.json
```

Only `splunk_dashboard` has a Docker healthcheck, so only it shows `(healthy)`.
`Up` alone is correct for the other two.

`HTTP 303` from Splunk is **success** — it is the redirect to the login page.

## 7.3 Is-it-up checks, one by one

### Containers

```bash
sudo docker ps                                            # running only
sudo docker ps -a                                         # includes stopped/crashed
sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

### Splunk specifically

```bash
# Status
sudo docker ps --filter name=splunk_dashboard --format '{{.Names}}\t{{.Status}}'

# Boot progress — Splunk takes 2-5 min on a B2s
sudo docker logs -f splunk_dashboard
sudo docker logs --tail 20 splunk_dashboard

# HTTP reachable from the VM
curl -I http://localhost:8000

# HTTP reachable from outside (run on your laptop)
curl -I http://<VM_PUBLIC_IP>:8000
```

Then browse to **`http://<VM_PUBLIC_IP>:8000`** and log in as `admin`.

### Cowrie

```bash
sudo docker logs cowrie_honeypot --tail 50

# Confirm listeners came up — want both lines
sudo docker logs cowrie_honeypot 2>&1 | grep -E 'Ready to accept'
#   [-] Ready to accept SSH connections
#   [-] Ready to accept Telnet connections
```

### Dionaea

```bash
sudo docker logs dionaea_honeypot --tail 50
sudo tail -50 ~/honeynet/dionaea-logs/dionaea-errors.log
```

### Ports, from outside

```bash
# From Kali or any external host. -Pn is REQUIRED (Azure drops ICMP).
nmap -Pn -p 21,2121,2222,2223,8000 <VM_PUBLIC_IP>
```

Expected: all five `open`. If `21` shows `filtered` but `2121` is `open`, your
network is blocking outbound FTP — use `2121`.

## 7.4 Watching attacks live

The demo-friendly commands. Run these in a second MobaXterm tab while attacking
from another machine.

**Raw event stream**
```bash
tail -f ~/honeynet/cowrie-logs/cowrie.json
```

**Just the commands attackers type** — the crowd-pleaser
```bash
tail -f ~/honeynet/cowrie-logs/cowrie.json \
| python3 -c "import sys, json; [print(f'[{json.loads(l)[\"timestamp\"][11:19]}] {json.loads(l)[\"src_ip\"]} > {json.loads(l)[\"input\"]}') for l in sys.stdin if 'cowrie.command.input' in l]"
```

**Same thing with `jq`, if installed**
```bash
tail -f ~/honeynet/cowrie-logs/cowrie.json \
| jq -r 'select(.eventid=="cowrie.command.input") | "[\(.timestamp[11:19])] \(.src_ip) » \(.input)"'
```

**Every command captured so far**
```bash
grep "cowrie.command.input" ~/honeynet/cowrie-logs/cowrie.json \
| python3 -c "import sys, json; [print(json.loads(line)['input']) for line in sys.stdin]"
```

**Successful logins**
```bash
grep "cowrie.login.success" ~/honeynet/cowrie-logs/cowrie.json \
| python3 -c "import sys,json;[print(json.loads(l)['src_ip'], json.loads(l)['username'], json.loads(l)['password']) for l in sys.stdin]"
```

**Live Dionaea, minus the SIP noise**
```bash
tail -f ~/honeynet/dionaea-logs/dionaea.log | grep -v 'Cleanup'
```

## 7.4b Stopping and restarting the VM

Deallocating the VM from the Azure portal is safe for your data — but two things
bite on the way back up. Read this before restarting.

### What survives a VM stop

Stopping the VM is **not** `docker compose down`. The OS disk persists, and every
container carries `restart: always`, so Docker brings them back on boot with
their writable layers intact.

| Data | Survives VM stop? | Notes |
|---|---|---|
| Cowrie logs, TTY replays, payloads | ✅ | Anonymous volume on the OS disk |
| Splunk index — every event | ✅ | Container layer; only `compose down` destroys it |
| `mousas_honeynet` dashboard | ✅ | Same |
| Dionaea captures + bistreams | ✅ | Same |
| Config, systemd unit, cron | ✅ | On disk |

The persistence gap in [F-03](09-findings-and-fixes.md) is triggered by
`docker compose down`, **not** by stopping the VM. Do not confuse the two.

### ⚠️ 1. The public IP will change

Deallocating releases a *dynamic* public IP. On next boot Azure assigns a new
one — every bookmark, SSH config entry and shared link breaks.

```bash
# Get the new address
az vm list-ip-addresses -g rg-honeynet-demo -n mousahoneypot -o table
# or: Azure portal > Virtual machines > <VM> > Overview > Public IP address
```

Make it permanent so this stops recurring:

```bash
az network public-ip update -g rg-honeynet-demo -n <public-ip-name> \
  --allocation-method Static
```

### ⚠️ 2. The duplicate-tail bug fires on boot

This is the one that actually costs you data quality. If the cron `@reboot` entry
from [F-02](09-findings-and-fixes.md) is still present, **both** it and
`honeynet-sync.service` start a `tail -F` against the same log, both appending to
the same destination. Every Cowrie event is then written twice and indexed twice.

It stays latent until a reboot — so the *first restart after installing the
systemd unit* is when it appears.

```bash
# Remove the cron entry (the systemd unit already handles boot)
crontab -e        # delete the "@reboot ... sync_logs.sh" line
crontab -l        # confirm it is gone
```

### Restart checklist

```bash
# 1. Start the VM (portal, or:)
az vm start -g rg-honeynet-demo -n mousahoneypot

# 2. Get the new IP, then SSH in
az vm list-ip-addresses -g rg-honeynet-demo -n mousahoneypot -o table
ssh -i <key>.pem azureuser@<NEW_IP>

# 3. Kill the duplicate bridge BEFORE trusting any new data
crontab -l | grep honeynet          # if present, crontab -e and delete it
ps aux | grep '[t]ail -F' | grep -c cowrie   # MUST be exactly 1

# 4. If it is 2 or more, clean up:
sudo pkill -f "tail -F /var/lib/docker/volumes"
sudo systemctl restart honeynet-sync.service
ps aux | grep '[t]ail -F' | grep -c cowrie   # now 1

# 5. Normal health check
sudo docker ps --format 'table {{.Names}}\t{{.Status}}'
curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://localhost:8000   # 303
ls -lh ~/honeynet/cowrie-logs/cowrie.json

# 6. Confirm your history is still indexed (not just new events)
#    In Splunk:  index=main sourcetype=cowrie:json earliest=-120d | stats count
#    Expect your full historical count, not a handful.

# 7. Update the NSG if your own public IP changed too — ports 22 and 8000
#    are scoped to it.
```

Splunk needs 2–5 minutes to become `(healthy)` after boot. An early `curl`
returning `000` or `502` is normal; re-check before assuming breakage.

### Check for duplicates that already got indexed

```spl
index=main sourcetype=cowrie:json
| stats count by _raw
| where count > 1
| sort - count
```

Identical raw events with `count > 1` are duplicates. There is no clean
retroactive fix short of re-indexing — prevent it by removing the cron entry.

## 7.5 Start, stop, restart

```bash
cd ~/honeynet

# Start everything
sudo docker compose up -d

# Stop (keeps volumes; Splunk index is still lost — see F-03)
sudo docker compose down

# ⚠️ DESTRUCTIVE: also deletes volumes = all Cowrie logs, TTY replays, payloads
sudo docker compose down -v

# One service
sudo docker restart cowrie_honeypot
sudo docker restart splunk_dashboard
sudo docker restart dionaea_honeypot

# Recreate one service from a changed compose file
sudo docker compose up -d --force-recreate cowrie

# Rebuild the custom Cowrie image (only if using build: instead of image:)
sudo docker compose build --no-cache cowrie
sudo docker compose up -d cowrie
```

> After **any** `compose down`/`up` or `--force-recreate` of Cowrie, the
> anonymous volume ID changes. Always restart the bridge afterwards:
>
> ```bash
> sudo systemctl restart honeynet-sync.service
> ```

### Log bridge

```bash
sudo systemctl status  honeynet-sync.service
sudo systemctl restart honeynet-sync.service
sudo systemctl enable  honeynet-sync.service
sudo systemctl daemon-reload            # after editing the unit file
sudo journalctl -u honeynet-sync.service -n 50 --no-pager
```

### Docker daemon

```bash
sudo systemctl status  docker
sudo systemctl start   docker
sudo systemctl enable  docker
```

## 7.6 Forensics

### TTY session replay

```bash
# List recordings — filename is the SHA-256 of the stream
V=$(sudo docker inspect cowrie_honeypot \
      --format='{{range .Mounts}}{{if eq .Destination "/cowrie/cowrie-git/var"}}{{.Source}}{{end}}{{end}}')
sudo ls -la $V/lib/cowrie/tty/

# Replay — plays back with original keystroke timing
sudo docker exec -it cowrie_honeypot \
  /cowrie/cowrie-env/bin/python3 -m cowrie.scripts.playlog /cowrie/cowrie-git/var/lib/cowrie/tty/<FILENAME>
```

Pick a large file (20 KB+) for a demo — small ones are usually bots that
connected and disconnected. Currently 19 recordings exist; the largest is 46 KB.

> Older notes use `/home/cowrie/cowrie-env/bin/python3 -m cowrie.scripts.playlog`. That path does not exist
> inside the container. Use `/cowrie/…`.

### Captured payloads

```bash
V=$(sudo docker inspect cowrie_honeypot \
      --format='{{range .Mounts}}{{if eq .Destination "/cowrie/cowrie-git/var"}}{{.Source}}{{end}}{{end}}')

sudo ls -1 $V/lib/cowrie/downloads | wc -l                 # 31
sudo du -sh $V/lib/cowrie/downloads                        # 16M
sudo sh -c "find $V/lib/cowrie/downloads -type f -exec sha256sum {} \;"
```

Filenames are already SHA-256, so:

```
https://www.virustotal.com/gui/search/<filename>
```

🚨 **Real malware.** Do not execute. Do not copy to a Windows host casually. Do
not commit.

### Dionaea captures

```bash
sudo docker exec dionaea_honeypot find /opt/dionaea/var/lib/dionaea -type f | sort
sudo docker exec dionaea_honeypot \
  find /opt/dionaea/var/lib/dionaea/binaries -type f -exec sha256sum {} \; 2>/dev/null
```

## 7.7 Troubleshooting

### Splunk will not load

```bash
sudo docker ps                          # is it even running?
sudo docker logs splunk_dashboard --tail 80
curl -I http://localhost:8000           # reachable locally?
```

| Cause | Check |
|---|---|
| Still starting | Status shows `(starting)`. Wait up to 5 min. |
| Missing licence flags | Boot-loops. Needs **both** `SPLUNK_START_ARGS` and `SPLUNK_GENERAL_TERMS`. |
| VM too small | `free -h`, `docker stats`. Needs ~4 GB. |
| NSG blocks 8000 | Works on `localhost`, not from outside. |
| Using HTTPS | Use `http://`. |
| IP changed | See [doc 02](02-azure-and-host-setup.md). |

### Splunk has no Cowrie events

Full procedure in [doc 06 §6.7](06-log-pipeline.md). Fast version:

```bash
sudo systemctl restart honeynet-sync.service
ls -lh ~/honeynet/cowrie-logs/cowrie.json      # is it growing?
sudo docker exec splunk_dashboard ls -la /data/cowrie/log
```

Then generate fresh traffic and search `index=main sourcetype=cowrie:json
earliest=-15m`.

### Cowrie will not start

```bash
sudo docker logs cowrie_honeypot --tail 100
sudo docker compose up -d --force-recreate cowrie
```

### Attacker cannot see or read the honeytokens

Expected as currently deployed — see [finding F-01](09-findings-and-fixes.md).
`ls` shows the files; `cat` returns empty.

Verify what Cowrie actually has:

```bash
# What Cowrie reads for file CONTENTS
sudo docker cp cowrie_honeypot:/cowrie/cowrie-git/honeyfs - | tar -tv | grep home
# (no output = the honeyfs mount is not effective)

# Where the custom content actually sits
sudo docker cp cowrie_honeypot:/home/cowrie/cowrie-git/honeyfs - | tar -tv | grep phil
```

### Login with the documented password fails

Also expected. The built-in default credential DB **denies** `root`/`123456`.
Use `root` / `admin`. Full explanation in [doc 03 §3.4](03-cowrie-config.md).

### FTP port 21 filtered

```bash
sudo ss -tulpen | grep -E ':(21|2121)\b'
sudo docker logs dionaea_honeypot --tail 100
```

If the port is open on the VM but unreachable from your client, your ISP is
blocking outbound 21. Use `2121`. Do not burn demo time on it — pivot to Cowrie.

### Disk filling up

```bash
df -h /
sudo du -sh /var/lib/docker/volumes/* 2>/dev/null | sort -h | tail -10
sudo docker system df
```

Cleanup (⚠️ `-a` removes unused images; `--volumes` removes unused volumes —
make sure Cowrie's is attached to a running container first):

```bash
sudo docker system prune -a
```

There are 60+ orphaned anonymous volumes on this host from earlier rebuilds.
They are safe to remove **while the stack is running**:

```bash
sudo docker volume prune          # removes only volumes with no container attached
```

## 7.8 Backups

The two things that are hard to recreate:

```bash
# 1. The dashboard (NOT persisted — container layer only)
sudo docker exec -u root splunk_dashboard \
  cat /opt/splunk/etc/users/admin/search/local/data/ui/views/mousas_honeynet.xml \
  > ~/honeynet/mousas_honeynet.backup.xml

# 2. The collected telemetry
V=$(sudo docker inspect cowrie_honeypot \
      --format='{{range .Mounts}}{{if eq .Destination "/cowrie/cowrie-git/var"}}{{.Source}}{{end}}{{end}}')
sudo tar czf ~/honeynet-data-$(date +%F).tar.gz \
  -C "$V" log lib \
  -C /home/azureuser/honeynet dionaea-logs
```

Pull them down over MobaXterm's SFTP pane, or:

```bash
scp -i mousahoneypot_key.pem \
  azureuser@<VM_PUBLIC_IP>:~/honeynet-data-*.tar.gz .
```

## 7.9 Command index

Everything in one place, grouped by intent.

| Intent | Command |
|---|---|
| Connect | `ssh -i mousahoneypot_key.pem azureuser@<VM_PUBLIC_IP>` |
| Splunk UI | `http://<VM_PUBLIC_IP>:8000` |
| Dashboard | `http://<VM_PUBLIC_IP>:8000/en-US/app/search/mousas_honeynet` |
| Containers | `sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'` |
| Splunk up? | `curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000` |
| Splunk boot | `sudo docker logs -f splunk_dashboard` |
| Listeners | `sudo ss -tulpn \| grep -E ':(21\|2121\|2222\|2223\|8000)\b'` |
| Bridge state | `sudo systemctl status honeynet-sync.service` |
| Restart bridge | `sudo systemctl restart honeynet-sync.service` |
| Watch events | `tail -f ~/honeynet/cowrie-logs/cowrie.json` |
| Watch commands | `grep "cowrie.command.input" ~/honeynet/cowrie-logs/cowrie.json \| python3 -c "import sys, json; [print(json.loads(line)['input']) for line in sys.stdin]"` |
| Find volume | `sudo docker inspect cowrie_honeypot --format='{{range .Mounts}}{{if eq .Destination "/cowrie/cowrie-git/var"}}{{.Source}}{{end}}{{end}}'` |
| Start stack | `cd ~/honeynet && sudo docker compose up -d` |
| Stop stack | `cd ~/honeynet && sudo docker compose down` |
| Scan from outside | `nmap -Pn -p 21,2121,2222,2223,8000 <VM_PUBLIC_IP>` |
| Firewall | `sudo ufw status verbose` (currently `inactive` — by design) |

---

Next: [08 — Demo playbook](08-demo-playbook.md)
