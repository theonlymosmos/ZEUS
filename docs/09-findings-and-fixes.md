# 09 — Findings & fixes

Six defects found while documenting the live deployment. Each was verified
against the running system; none is speculative. Ordered by impact.

**Nothing here has been changed on the VM.** The deployment is exactly as it was
found. These are recommendations with verified evidence and tested-shape fixes.

| ID | Severity | Finding |
|---|---|---|
| [F-01](#f-01) | 🔴 High | Volume paths target `/home/cowrie/…`; Cowrie reads `/cowrie/…`. Honeytoken contents and credential policy silently inert. |
| [F-02](#f-02) | 🟠 Medium | Duplicate log bridge (cron + systemd) double-indexes every event after reboot. |
| [F-03](#f-03) | 🟠 Medium | Splunk index, dashboard, and Dionaea captures are not persisted. |
| [F-04](#f-04) | 🟠 Medium | Splunk admin password hardcoded in `docker-compose.yml`; `.env` ignored. |
| [F-05](#f-05) | 🟡 Low | Splunk Web on plain HTTP, apparently internet-reachable. |
| [F-06](#f-06) | 🟡 Low | Deployment directory is world-writable (0777). |

---

<a name="f-01"></a>
## F-01 — Container path mismatch 🔴

### What is wrong

Every Cowrie volume mount targets `/home/cowrie/cowrie-git/…`. The
`cowrie/cowrie` image installs Cowrie at `/cowrie/cowrie-git/…`
(`COWRIE_HOME=/cowrie`). Docker creates the `/home/cowrie/…` tree, mounts the
files there, and Cowrie never reads any of it.

Four mounts are affected:

| Mount | Intended effect | Actual effect |
|---|---|---|
| `./cowrie-logs:/home/cowrie/…/var/log/cowrie` | Logs on host | ❌ Logs go to an anonymous volume |
| `./cowrie-custom/honeyfs:/home/cowrie/…/honeyfs` | Honeytoken **contents** | ❌ Stock honeyfs used |
| `./cowrie-custom/userdb.txt:/home/cowrie/…/etc/userdb.txt` | Credential policy | ❌ Built-in defaults used |
| `./cowrie-custom/lab_fs.pickle:/home/cowrie/…/share/cowrie/lab_fs.pickle` | Fake filesystem **metadata** | ✅ **Works** — `COWRIE_SHELL_FILESYSTEM` points here |

The pickle mount works by coincidence: the env var explicitly names the
`/home/cowrie/…` path, so Cowrie is told to look exactly where the mount landed.

### Evidence

Cowrie cannot find `userdb.txt`:

```
2026-04-28T14:53:19+0000 [HoneyPotSSHTransport,0,…] Could not read etc/userdb.txt, default database activated
```

No custom config was loaded at all:

```
2026-04-28T14:49:23+0000 [-] Reading configuration from ['/cowrie/cowrie-git/etc/cowrie.cfg.dist']
```

The honeyfs Cowrie reads has no `/home`:

```console
$ sudo docker cp cowrie_honeypot:/cowrie/cowrie-git/honeyfs - | tar -tv
drwxr-xr-x 999/999   0  honeyfs/etc/
-rw-r--r-- 999/999   0  honeyfs/etc/issue.net       ← 0 bytes = stock
-rw-r--r-- 999/999 286  honeyfs/etc/motd            ← stock
drwxr-xr-x 999/999   0  honeyfs/proc/
                                                     ← no honeyfs/home/
```

The custom honeyfs sits unused in an orphan directory:

```console
$ sudo docker cp cowrie_honeypot:/home/cowrie/cowrie-git/honeyfs - | tar -tv
-rwxrwxrwx 1000/114  93  honeyfs/home/phil/Project_Zeus_Master_DB_Backup.sql
-rwxrwxrwx 1000/114 184  honeyfs/home/phil/notes.txt
-rwxrwxrwx 1000/114 240  honeyfs/home/phil/deploy.sh
-rwxrwxrwx 1000/114 189  honeyfs/home/phil/.bash_history
```

`/cowrie/cowrie-git/etc` is an anonymous volume with only stock files:

```console
$ sudo ls /var/lib/docker/volumes/30328f27…/_data
.gitignore  cowrie.cfg.dist  userdb.example
```

### Impact

- **Honeytokens are visible but empty.** `ls /home/phil` lists them (pickle
  works), `cat` returns nothing (honeyfs does not). Attackers ran `cat
  Project_Zeus_Master_DB_Backup.sql` six times and got nothing.
- **The credential policy is not the one configured.** `root:x:*` was intended;
  Cowrie's defaults are in force, which explicitly **deny `root`/`123456`** —
  the password in the demo scripts.
- **Custom banner and MOTD never served.**
- **Logs land in a randomly-named anonymous volume**, requiring the entire
  `honeynet-sync.service` bridge ([doc 06](06-log-pipeline.md)) to exist at all.

### Fix — option A: correct the paths (minimal change)

```yaml
  cowrie:
    image: cowrie/cowrie:latest
    container_name: cowrie_honeypot
    hostname: prod-app-01
    ports:
      - "2222:2222"
      - "2223:2223"
    environment:
      - COWRIE_TELNET_ENABLED=yes
      - COWRIE_SHELL_FILESYSTEM=/cowrie/cowrie-git/share/cowrie/lab_fs.pickle
    volumes:
      - ./cowrie-logs:/cowrie/cowrie-git/var/log/cowrie
      - cowrie-var:/cowrie/cowrie-git/var/lib/cowrie
      - ./cowrie-custom/honeyfs:/cowrie/cowrie-git/honeyfs:ro
      - ./cowrie-custom/userdb.txt:/cowrie/cowrie-git/etc/userdb.txt:ro
      - ./cowrie-custom/lab_fs.pickle:/cowrie/cowrie-git/share/cowrie/lab_fs.pickle:ro
    restart: always
    networks:
      - honeynet

volumes:
  cowrie-var:
```

Every `/home/cowrie/` became `/cowrie/`, including inside the env var. A named
volume (`cowrie-var`) replaces the anonymous one so TTY replays and payloads have
a stable, findable home.

> ⚠️ Mounting a single file at `/cowrie/cowrie-git/etc/userdb.txt` when
> `/cowrie/cowrie-git/etc` is a declared image VOLUME can be fragile. If
> `userdb.txt` still fails to load after this change, use option B.

### Fix — option B: use the custom image (recommended)

`config/cowrie-custom/Dockerfile` already exists and already uses the correct
paths. Switch Compose to build it:

```yaml
  cowrie:
    build:
      context: ./cowrie-custom
    image: honeynet-cowrie-custom:latest
    container_name: cowrie_honeypot
    hostname: prod-app-01
    ports:
      - "2222:2222"
      - "2223:2223"
    environment:
      - COWRIE_TELNET_ENABLED=yes
    volumes:
      - ./cowrie-logs:/cowrie/cowrie-git/var/log/cowrie
      - cowrie-var:/cowrie/cowrie-git/var/lib/cowrie
    restart: always
    networks:
      - honeynet
```

The Dockerfile bakes honeyfs, `userdb.txt` and the pickle into the image and sets
`COWRIE_SHELL_FILESYSTEM` itself, so no content mounts are needed. Better for a
honeypot: fixed layout, fewer host-filesystem paths reachable from inside the
container, no ownership friction.

### Migration — preserving existing data

The current anonymous volume holds 2,987 events, 19 TTY replays and 31 payloads.
Do not lose them.

```bash
cd ~/honeynet

# 1. Back up everything first
V=$(sudo docker inspect cowrie_honeypot \
      --format='{{range .Mounts}}{{if eq .Destination "/cowrie/cowrie-git/var"}}{{.Source}}{{end}}{{end}}')
sudo tar czf ~/cowrie-data-backup-$(date +%F).tar.gz -C "$V" log lib
sudo cp ~/honeynet/cowrie-logs/cowrie.json ~/cowrie-json-backup-$(date +%F).json

# 2. Back up the Splunk dashboard (see F-03 — it is not persisted)
sudo docker exec -u root splunk_dashboard \
  cat /opt/splunk/etc/users/admin/search/local/data/ui/views/mousas_honeynet.xml \
  > ~/mousas_honeynet.backup.xml

# 3. Edit docker-compose.yml per option A or B

# 4. Recreate ONLY cowrie (leave Splunk running so the index survives)
sudo docker compose up -d --force-recreate cowrie

# 5. Restore historical payloads/replays into the new named volume
NEW=$(sudo docker volume inspect honeynet_cowrie-var --format '{{.Mountpoint}}')
sudo tar xzf ~/cowrie-data-backup-*.tar.gz -C /tmp
sudo cp -an /tmp/lib/cowrie/. "$NEW"/

# 6. Disable the bridge — no longer needed
sudo systemctl disable --now honeynet-sync.service

# 7. Verify
sudo docker logs cowrie_honeypot --tail 30 | grep -i userdb   # should be silent now
ls -lh ~/honeynet/cowrie-logs/cowrie.json                     # written directly by Cowrie
```

### Verification

```bash
# Honeyfs now visible where Cowrie reads it
sudo docker cp cowrie_honeypot:/cowrie/cowrie-git/honeyfs - | tar -tv | grep phil
# expect: honeyfs/home/phil/Project_Zeus_Master_DB_Backup.sql  etc.

# No more "Could not read etc/userdb.txt"
sudo docker logs cowrie_honeypot 2>&1 | grep -c 'Could not read etc/userdb.txt'
# expect: 0
```

Then log into the trap and confirm `cat Project_Zeus_Master_DB_Backup.sql`
returns the SQL.

---

<a name="f-02"></a>
## F-02 — Duplicate log bridge 🟠

### What is wrong

Both a cron `@reboot` job and a systemd service start the log bridge:

```console
$ crontab -l | grep honeynet
@reboot sleep 30 && /home/azureuser/honeynet/sync_logs.sh
```

```console
$ sudo systemctl is-enabled honeynet-sync.service
enabled
```

After every reboot, two `tail -F` processes append the same source to the same
destination. Every Cowrie event is written twice and indexed twice.

The cron entry predates the systemd unit and was never removed. This is the
"triplets" duplicate-event bug from the original build notes, still latent.

*(Currently only one tail is running — the host has not rebooted since the
systemd unit was installed. The bug fires on the next reboot.)*

### Impact

Event counts inflate, `| stats count` misreports, `top` rankings distort, and
the geolocation map over-weights whichever attacker was active at reboot time.

### Fix

```bash
crontab -e      # delete the @reboot honeynet line
crontab -l      # verify
```

The systemd unit is `enabled` with `Restart=always` and handles boot on its own.

### Verification

```bash
sudo reboot
# wait, reconnect
ps aux | grep '[t]ail -F' | grep cowrie | wc -l    # expect exactly 1
```

Check for duplicates already indexed:

```spl
index=main sourcetype=cowrie:json | stats count by _raw | where count > 1
```

---

<a name="f-03"></a>
## F-03 — No persistence for Splunk state or Dionaea captures 🟠

### What is wrong

The Splunk service mounts no volume at `/opt/splunk/var` (index) or
`/opt/splunk/etc` (config, including the dashboard). Dionaea mounts nothing at
`/opt/dionaea/var/lib` (captured binaries). All three live in container writable
layers.

A single `docker compose down` — a routine operation, not a destructive one —
destroys:

- every indexed event
- the `mousas_honeynet` dashboard
- all Dionaea-captured malware and bistreams

The dashboard is at particular risk: it was created through the GUI, so it lives
as a *private user object* at
`/opt/splunk/etc/users/admin/search/local/data/ui/views/mousas_honeynet.xml`.

### Fix

```yaml
  dionaea:
    volumes:
      - ./dionaea-logs:/opt/dionaea/var/log/dionaea
      - dionaea-lib:/opt/dionaea/var/lib          # ← add
      - dionaea-etc:/opt/dionaea/etc              # ← add

  splunk:
    volumes:
      - splunk-etc:/opt/splunk/etc                # ← add
      - splunk-var:/opt/splunk/var                # ← add
      - ./cowrie-logs:/data/cowrie/log:ro
      - ./dionaea-logs:/data/dionaea/log:ro
      - dionaea-lib:/data/dionaea/lib:ro          # ← lets Splunk see captures
      - ./splunk_apps/honeynet_inputs:/opt/splunk/etc/apps/honeynet_inputs:rw

volumes:
  dionaea-etc:
  dionaea-lib:
  splunk-etc:
  splunk-var:
```

> **Order matters.** Mounting an empty volume at `/opt/splunk/etc` on first run
> lets Splunk populate it. But `honeynet_inputs` is mounted *inside* that path —
> Docker applies the more specific mount second, so the app mount wins. This
> works, but verify after the change:
>
> ```bash
> sudo docker exec splunk_dashboard ls /opt/splunk/etc/apps/ | grep honeynet
> ```

**Do this before applying:** back up the dashboard and the index. Recreating the
Splunk container with new volumes *will* start it with an empty index.

```bash
sudo docker exec -u root splunk_dashboard \
  cat /opt/splunk/etc/users/admin/search/local/data/ui/views/mousas_honeynet.xml \
  > ~/mousas_honeynet.backup.xml
```

### Also: move the dashboard into the app

Private user objects are fragile. Ship the dashboard with the deployment
instead — then it is version-controlled and survives everything:

```bash
mkdir -p ~/honeynet/splunk_apps/honeynet_inputs/default/data/ui/views
cp ~/mousas_honeynet.backup.xml \
   ~/honeynet/splunk_apps/honeynet_inputs/default/data/ui/views/mousas_honeynet.xml
sudo docker restart splunk_dashboard
```

The dashboard then lives at
`http://<VM_PUBLIC_IP>:8000/en-US/app/honeynet_inputs/mousas_honeynet`.
Set `is_visible = 1` in `app.conf` to give it a menu entry.

---

<a name="f-04"></a>
## F-04 — Splunk password hardcoded 🟠

### What is wrong

The live `docker-compose.yml` contains the admin password in cleartext:

```yaml
      - SPLUNK_PASSWORD=<REDACTED-see-your-.env>
```

Meanwhile `.env` exists, contains a *different* password, and is never read:

```console
$ cat ~/honeynet/.env
SPLUNK_PASSWORD=ChangeMe_StrongPassword_2026!
```

Compose auto-loads `.env` and substitutes `${SPLUNK_PASSWORD}` — but the compose
file uses a literal, so `.env` has no effect. The two values disagree, which
makes it easy to lock yourself out by trusting the wrong file.

The same password also appears in project notes and shell history:

```console
$ grep -c '<REDACTED>' ~/.bash_history
1
```

### Impact

Committing this compose file publishes working credentials for an
internet-reachable admin console (see F-05). The mismatch between `.env` and the
compose literal is also an operational trap.

### Fix

```yaml
      - SPLUNK_PASSWORD=${SPLUNK_PASSWORD:?SPLUNK_PASSWORD must be set in .env}
```

The `:?` form makes Compose **fail loudly** if the variable is missing, rather
than starting Splunk with an empty password.

```bash
cd ~/honeynet
# set the real password in .env
nano .env
chmod 600 .env

# rotate the password, since the old one is in git-adjacent files and history
sudo docker exec -it splunk_dashboard \
  /opt/splunk/bin/splunk edit user admin -password '<NEW>' -auth admin:'<OLD>'

# then recreate with the env-driven config
sudo docker compose up -d --force-recreate splunk
```

Scrub the history:

```bash
grep -n '<REDACTED>' ~/.bash_history   # find it
# edit ~/.bash_history to remove those lines, then:
history -c && history -w
```

The version in this repository has already been redacted to
`${SPLUNK_PASSWORD}`, and `.gitignore` excludes `.env`.

---

<a name="f-05"></a>
## F-05 — Splunk Web exposed over plain HTTP 🟡

### What is wrong

Splunk Web binds `0.0.0.0:8000` and serves **unencrypted HTTP**:

```console
$ sudo ss -tulpn | grep 8000
tcp LISTEN 0 4096 0.0.0.0:8000 0.0.0.0:*  users:(("docker-proxy",pid=1684,fd=7))

$ curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000
303
```

Combined with F-04, an admin console with a known password is reachable over a
cleartext protocol. Whether it is reachable from the *internet* depends on the
Azure NSG — but the project's own working notes reference
`http://<VM_PUBLIC_IP>:8000/...` as a browser URL from outside, which implies the
rule is open.

### Impact

Anyone who can reach port 8000 can attempt login. Anyone on the network path can
read the session cookie. The SIEM is the one component that must *not* be
compromised — it holds the evidence.

### Fix — layered, easiest first

**1. Restrict at the NSG (do this today).** Change the inbound rule for 8000 from
`Any` to your own public IP `/32`. One click, eliminates internet exposure.

**2. Do not publish the port at all; tunnel instead.** Change the Compose port
binding to loopback:

```yaml
    ports:
      - "127.0.0.1:8000:8000"
```

Then reach it over an SSH tunnel:

```bash
ssh -i mousahoneypot_key.pem -L 8000:localhost:8000 azureuser@<VM_PUBLIC_IP>
# browse to http://localhost:8000
```

Splunk becomes unreachable from the internet entirely. This is the right answer
for a single-operator deployment and costs nothing.

**3. Enable TLS**, if the console must stay published:

```bash
sudo docker exec -it splunk_dashboard bash -c \
  'echo -e "[settings]\nenableSplunkWebSSL = true" >> /opt/splunk/etc/system/local/web.conf'
sudo docker restart splunk_dashboard
# then use https://<VM_PUBLIC_IP>:8000
```

---

<a name="f-06"></a>
## F-06 — World-writable deployment directory 🟡

### What is wrong

```console
$ ls -la ~ | grep honeynet
drwxrwxrwx 6 azureuser docker 4096 Apr 28 18:35 honeynet
```

Mode 0777, applied during troubleshooting (`sudo chmod -R 777 ~/honeynet`) to
resolve a container-writes-to-bind-mount permission error. The bridged log file
inherited it:

```console
-rwxrwxrwx 1 azureuser azureuser 2647186 Aug  7 09:14 cowrie.json
```

### Impact

Any local user can rewrite the honeypot's configuration or truncate its logs.
With one human user on the host, real-world risk is low — but this is a system
you *expect* to be attacked, and world-writable evidence files are exactly what
an attacker who achieved container escape would target.

### Fix

```bash
cd ~
chmod 755 honeynet
chmod -R 755 honeynet/cowrie-custom honeynet/splunk_apps
chmod 600 honeynet/.env
chmod 755 honeynet/sync_logs.sh

# Log dirs need group-write for the container's user; use group ownership,
# not world-write
sudo chown -R azureuser:docker honeynet/cowrie-logs honeynet/dionaea-logs
chmod 775 honeynet/cowrie-logs honeynet/dionaea-logs
chmod 664 honeynet/cowrie-logs/cowrie.json
```

If a container then hits permission errors, fix it with `user:` in Compose or by
matching uid/gid — not by widening to 0777:

```yaml
  cowrie:
    user: "1000:1000"
```

Applying F-01 option B (bake content into the image) removes most bind mounts and
makes this moot.

---

## Recommended order of work

1. **F-04** — rotate the password, move it to `.env`. 5 minutes, biggest
   security return.
2. **F-05 step 1** — restrict NSG port 8000 to your IP. 2 minutes, one click.
3. **F-03** — add persistence volumes, back up the dashboard **first**. 15
   minutes. Do this before F-01, so F-01's container recreation does not cost you
   the index.
4. **F-01** — fix the paths, ideally via the custom image. 20–30 minutes. Makes
   the honeytokens actually work and retires the log bridge.
5. **F-02** — delete the cron entry. 1 minute. Do it whenever.
6. **F-06** — tighten permissions. 5 minutes. Easiest right after F-01.

Back up before starting:

```bash
V=$(sudo docker inspect cowrie_honeypot \
      --format='{{range .Mounts}}{{if eq .Destination "/cowrie/cowrie-git/var"}}{{.Source}}{{end}}{{end}}')
sudo tar czf ~/honeynet-full-backup-$(date +%F).tar.gz \
  -C "$V" log lib \
  -C /home/azureuser honeynet
sudo docker exec -u root splunk_dashboard \
  cat /opt/splunk/etc/users/admin/search/local/data/ui/views/mousas_honeynet.xml \
  > ~/mousas_honeynet.backup.xml
```

---

Next: [10 — Observed attack data](10-observed-attack-data.md)
