# 06 — The log pipeline (a.k.a. the "ghost volume" problem)

This is the most non-obvious part of the deployment. If Splunk ever stops
receiving Cowrie events, the answer is almost certainly in this document.

## 6.1 The problem in one sentence

Cowrie writes its logs to a randomly-named anonymous Docker volume instead of the
directory the Compose file names, so Splunk — which watches that directory — sees
nothing.

## 6.2 How it happens

> **Prerequisite:** [doc 03 §3.2b](03-cowrie-config.md#32b-how-cowrie-is-installed-inside-the-container)
> documents Cowrie's install layout and its relative default paths — `log_path =
> var/log/cowrie`, `state_path = var/lib/cowrie`, both resolving inside the
> image's declared `VOLUME /cowrie/cowrie-git/var`. That is the mechanism behind
> everything below.

The Compose file says:

```yaml
    volumes:
      - ./cowrie-logs:/home/cowrie/cowrie-git/var/log/cowrie
```

Intent: Cowrie's log directory appears on the host at `~/honeynet/cowrie-logs`,
where Splunk's read-only mount can reach it.

Reality: the `cowrie/cowrie` image installs to `/cowrie/cowrie-git`, not
`/home/cowrie/cowrie-git`. So:

1. Docker creates `/home/cowrie/cowrie-git/var/log/cowrie` in the container and
   binds `~/honeynet/cowrie-logs` there. **Nothing ever writes to it.**
2. Cowrie writes to `/cowrie/cowrie-git/var/log/cowrie/cowrie.json`.
3. The image declares `VOLUME /cowrie/cowrie-git/var`. Since Compose gives Docker
   no host path for it, Docker creates an **anonymous volume** with a random
   64-hex-character name.
4. Cowrie's logs, TTY replays and captured payloads all land in that anonymous
   volume, at a path nobody configured and nobody is watching.

The current one:

```
/var/lib/docker/volumes/6e416b9fdb9b72e86a645442d148f992ab0dfd00004115c96b72c455305550a3/_data/
```

Verified:

```console
$ sudo docker inspect cowrie_honeypot --format '{{json .Mounts}}'
[
  { "Type": "volume",
    "Name": "6e416b9fdb9b72e86a645442d148f992ab0dfd00004115c96b72c455305550a3",
    "Source": "/var/lib/docker/volumes/6e416b9f.../_data",
    "Destination": "/cowrie/cowrie-git/var" },          ← where Cowrie ACTUALLY writes
  { "Type": "bind",
    "Source": "/home/azureuser/honeynet/cowrie-logs",
    "Destination": "/home/cowrie/cowrie-git/var/log/cowrie" },  ← where NOTHING writes
  ...
]
```

Two things make this genuinely nasty rather than merely wrong:

- **The name is random and changes.** Every `docker compose down` + `up`, every
  `--force-recreate`, mints a *new* anonymous volume with a *new* random name.
  Any hardcoded path breaks. The host's shell history shows at least two
  different volume IDs used over the project's life (`20502a84…`, then
  `6e416b9f…`).
- **Nothing errors.** Cowrie is happy. Docker is happy. Splunk is happy. The only
  symptom is an empty dashboard.

The same root cause breaks the honeyfs and `userdb.txt` mounts — see
[doc 03 §3.3](03-cowrie-config.md) and [finding F-01](09-findings-and-fixes.md).

## 6.3 The bridge

Rather than fix the paths, the deployment builds a bridge: find the anonymous
volume at runtime, and `tail -F` its log into the directory Splunk watches.

`config/sync_logs.sh`:

```bash
#!/bin/bash
# 1. Hunt for the ghost volume
NEW_PATH=$(sudo docker inspect cowrie_honeypot \
  --format='{{range .Mounts}}{{if eq .Destination "/cowrie/cowrie-git/var"}}{{.Source}}{{end}}{{end}}')

# 2. Check if the path exists
if [ -z "$NEW_PATH" ]; then
    echo "Container not ready. Systemd will retry in 10s..."
    exit 1
fi

# 3. Start the stream (No pkill, no backgrounding)
# We use 'exec' so the tail process replaces the script process
exec sudo tail -F $NEW_PATH/log/cowrie/cowrie.json >> /home/azureuser/honeynet/cowrie-logs/cowrie.json
```

Three good decisions in nine lines:

**It discovers the path instead of hardcoding it.** The `docker inspect
--format` template walks `.Mounts`, matches on the *destination*
`/cowrie/cowrie-git/var` — which is stable — and returns the *source*, which is
not. Survives every recreate.

**It exits non-zero when the container is not ready**, letting systemd's
`Restart=always` + `RestartSec=10` handle the retry loop. No sleep-poll.

**It uses `exec`.** The `tail` process *replaces* the script process rather than
becoming its child, so systemd's `MainPID` tracks `tail` directly. Stopping the
service actually stops the tail.

### Why `tail -F` and not `-f`

- `-f` follows a **file descriptor**. When Cowrie rotates `cowrie.json` at
  midnight, the old descriptor points at the renamed file and the tail goes
  permanently silent.
- `-F` follows a **filename**. On rotation it notices the inode changed, reopens
  the path, and keeps going.

Rotation is real here — the volume holds `cowrie.json.2026-04-28`,
`cowrie.json.2026-05-03`, `cowrie.json.2026-05-04`. With `-f` the pipeline would
have died on 28 April.

Observed in the service journal, working exactly as designed:

```
tail: '/var/lib/docker/volumes/6e416b9f.../log/cowrie/cowrie.json' has become inaccessible: No such file or directory
tail: '/var/lib/docker/volumes/6e416b9f.../log/cowrie/cowrie.json' has appeared;  following new file
```

## 6.4 The systemd unit

`config/systemd/honeynet-sync.service` → `/etc/systemd/system/honeynet-sync.service`:

```ini
[Unit]
Description=Honeynet Ghost Volume Sync
After=docker.service
Requires=docker.service

[Service]
ExecStart=/home/azureuser/honeynet/sync_logs.sh
Restart=always
RestartSec=10
User=root

[Install]
WantedBy=multi-user.target
```

| Directive | Why |
|---|---|
| `After=` / `Requires=docker.service` | Docker must be up before `docker inspect` can work |
| `Restart=always` | Covers container recreation, rotation edge cases, crashes |
| `RestartSec=10` | Retry cadence while waiting for the container |
| `User=root` | `/var/lib/docker/volumes` is mode 0700, root-owned |

Current state:

```console
$ sudo systemctl status honeynet-sync.service
● honeynet-sync.service - Honeynet Ghost Volume Sync
     Loaded: loaded (/etc/systemd/system/honeynet-sync.service; enabled; preset: enabled)
     Active: active (running) since Fri 2026-08-07 03:09:37 UTC; 6h ago
   Main PID: 1790 (sudo)
     CGroup: /system.slice/honeynet-sync.service
             ├─1790 sudo tail -F /var/lib/docker/volumes/6e416b9f.../log/cowrie/cowrie.json
             └─1803 tail -F /var/lib/docker/volumes/6e416b9f.../log/cowrie/cowrie.json
```

Installing it:

```bash
sudo cp config/systemd/honeynet-sync.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now honeynet-sync.service
sudo systemctl status honeynet-sync.service
```

## 6.5 ⚠️ The duplicate cron entry

A user crontab still contains:

```cron
@reboot sleep 30 && /home/azureuser/honeynet/sync_logs.sh
```

This predates the systemd unit and is now **actively harmful**. After every
reboot both the cron job and the systemd service start a `tail -F` against the
same source, both appending to the same destination — so every Cowrie event is
written to `cowrie.json` twice, and Splunk indexes it twice.

This is the "triplets" bug from the original build notes, resurrected. Symptom:
event counts roughly double, `| stats count` inflates, `top` rankings distort.

**Remove it:**

```bash
crontab -e     # delete the @reboot line
crontab -l     # verify it is gone
```

The systemd unit is `enabled` and handles boot on its own.

### Detecting duplicate tails

```bash
# Should show exactly ONE tail process (plus its sudo parent)
ps aux | grep '[t]ail -F' | grep cowrie
```

If more than one, kill the strays and restart the service cleanly:

```bash
sudo pkill -f "tail -F /var/lib/docker/volumes"
sudo systemctl restart honeynet-sync.service
```

### Detecting duplicates already in the index

```spl
index=main sourcetype=cowrie:json
| stats count by _raw
| where count > 1
| sort - count
```

Identical raw events with `count > 1` are duplicates. There is no clean
retroactive fix short of re-indexing; prevent it instead.

## 6.6 The complete path

```
  Cowrie (container)
  writes /cowrie/cowrie-git/var/log/cowrie/cowrie.json
        │
        ▼
  Anonymous Docker volume  6e416b9f…  (host: /var/lib/docker/volumes/…/_data)
        │
        │   honeynet-sync.service  →  sync_logs.sh  →  tail -F  >>
        ▼
  /home/azureuser/honeynet/cowrie-logs/cowrie.json   (host, ~2.6 MB)
        │
        │   docker-compose:  ./cowrie-logs:/data/cowrie/log:ro
        ▼
  /data/cowrie/log/cowrie.json   (inside splunk_dashboard)
        │
        │   inputs.conf  [monitor:///data/cowrie/log/cowrie.json]
        │   props.conf   KV_MODE=json, TIME_PREFIX/TIME_FORMAT
        ▼
  index=main  sourcetype=cowrie:json
```

Dionaea, by contrast, needs no bridge — its single bind mount is correct, so it
writes straight to `~/honeynet/dionaea-logs/` and Splunk picks it up directly.

## 6.7 Debugging a broken pipeline

Work down the chain. Each step tells you whether to keep going.

```bash
# 1. Is Cowrie writing at all?
V=$(sudo docker inspect cowrie_honeypot \
      --format='{{range .Mounts}}{{if eq .Destination "/cowrie/cowrie-git/var"}}{{.Source}}{{end}}{{end}}')
echo "volume: $V"
sudo ls -la $V/log/cowrie/

# 2. Is the bridge running?
sudo systemctl status honeynet-sync.service
ps aux | grep '[t]ail -F' | grep cowrie      # want exactly one

# 3. Is the host-side file growing?
ls -lh ~/honeynet/cowrie-logs/cowrie.json
tail -f ~/honeynet/cowrie-logs/cowrie.json   # Ctrl-C to stop

# 4. Can Splunk see it through the mount?
sudo docker exec splunk_dashboard ls -la /data/cowrie/log/

# 5. Is Splunk monitoring it?
sudo docker exec -it splunk_dashboard \
  /opt/splunk/bin/splunk list monitor -auth admin:'<PASSWORD>'

# 6. Is it indexed?
#    In Splunk Web:  index=main sourcetype=cowrie:json earliest=-24h | head 20
```

### Symptom → cause

| Symptom | Likely cause |
|---|---|
| Volume has no `cowrie.json` | Cowrie is not running, or never received a connection |
| Volume grows, host file does not | Bridge is dead → `systemctl restart honeynet-sync` |
| Bridge running but file static | Stale tail on an old volume ID → restart the service |
| Host file grows, Splunk sees nothing | Mount broken → recreate the Splunk container |
| Splunk sees the file, no events | `inputs.conf` not loaded, or wrong index/sourcetype |
| Events indexed, all at the same timestamp | `TIME_PREFIX`/`TIME_FORMAT` not applied |
| Every event appears twice | Duplicate tail — see §6.5 |

### The manual restart (the "known-good incantation")

Recorded here because it appears throughout the project's working notes:

```bash
# 1. Kill any old, broken tail processes
sudo pkill -f "tail -F /var/lib/docker/volumes"

# 2. Find the CURRENT active volume and start the pipe
NEW_PATH=$(sudo docker inspect cowrie_honeypot \
  --format='{{range .Mounts}}{{if eq .Destination "/cowrie/cowrie-git/var"}}{{.Source}}{{end}}{{end}}')
sudo tail -F $NEW_PATH/log/cowrie/cowrie.json >> ~/honeynet/cowrie-logs/cowrie.json &

# 3. Verify
tail -f ~/honeynet/cowrie-logs/cowrie.json
```

Prefer `sudo systemctl restart honeynet-sync.service` — it does the same thing
without leaving an orphaned background process attached to your SSH session.

## 6.8 The proper fix

The bridge is a well-built workaround for a problem that should not exist.
Correcting the volume paths removes the need for it entirely:

```yaml
  cowrie:
    volumes:
      - ./cowrie-logs:/cowrie/cowrie-git/var/log/cowrie    # note: /cowrie, not /home/cowrie
      - cowrie-var:/cowrie/cowrie-git/var/lib/cowrie       # named volume for tty/downloads
```

Then Cowrie writes directly to `~/honeynet/cowrie-logs/`, Splunk reads it
directly, and `honeynet-sync.service` can be disabled.

Full migration procedure — including how to preserve the existing 2,987 events
and 31 captured payloads — is in [finding F-01](09-findings-and-fixes.md).

---

Next: [07 — Operations runbook](07-operations-runbook.md)
