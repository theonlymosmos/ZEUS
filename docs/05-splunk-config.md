# 05 — Splunk configuration (SIEM)

## 5.1 Access

| | |
|---|---|
| **URL** | **`http://<VM_PUBLIC_IP>:8000`** |
| Username | `admin` |
| Password | Value of `SPLUNK_PASSWORD` in the Compose environment |
| Dashboard | `http://<VM_PUBLIC_IP>:8000/en-US/app/search/mousas_honeynet` |
| Index | `main` |
| Sourcetypes | `cowrie:json`, `cowrie:log`, `dionaea:log` |

**It is HTTP, not HTTPS.** `https://<VM_PUBLIC_IP>:8000` will not load. Splunk's
Docker image serves plain HTTP on 8000 unless SSL is explicitly configured.

Confirm it is up without a browser:

```console
$ curl -s -o /dev/null -w "%{http_code}\n" http://<VM_PUBLIC_IP>:8000
303
```

`303` is correct and healthy — it is the redirect to `/en-US/account/login`. A
`000` means nothing is listening or the NSG is blocking; a `502` means Splunk is
still starting.

## 5.2 The Compose service

```yaml
  splunk:
    image: splunk/splunk:latest
    container_name: splunk_dashboard
    hostname: splunk
    environment:
      - SPLUNK_START_ARGS=--accept-license
      - SPLUNK_GENERAL_TERMS=--accept-sgt-current-at-splunk-com
      - SPLUNK_PASSWORD=${SPLUNK_PASSWORD}
    ports:
      - "8000:8000"
    volumes:
      - ./cowrie-logs:/data/cowrie/log:ro
      - ./dionaea-logs:/data/dionaea/log:ro
      - ./splunk_apps/honeynet_inputs:/opt/splunk/etc/apps/honeynet_inputs:rw
    restart: always
    networks:
      - honeynet
```

### The two licence variables are mandatory

Without **both**, the container enters a restart loop that looks like a crash:

```
SPLUNK_START_ARGS=--accept-license
SPLUNK_GENERAL_TERMS=--accept-sgt-current-at-splunk-com
```

Older Splunk images needed only the first. Current images also require
acceptance of the Splunk General Terms, and the failure mode is a silent
boot-loop rather than a useful error. This cost real debugging time during the
original build.

### The log mounts are read-only

`:ro` on both sensor log mounts. Splunk indexes them; it must never be able to
modify or truncate them. This is correct and should stay.

### ⚠️ Splunk's own state is not persisted

There is no volume at `/opt/splunk/var` (the index) or `/opt/splunk/etc` (the
configuration, including the dashboard). Both live in the container's writable
layer.

`docker compose down` — not even `down -v` — destroys:

- every indexed event
- the `mousas_honeynet` dashboard
- the admin user's saved searches and preferences

The dashboard XML is preserved in this repo at
`config/splunk/dashboards/mousas_honeynet.xml`, but that is a manual snapshot.
Fix properly per [finding F-03](09-findings-and-fixes.md):

```yaml
    volumes:
      - splunk-etc:/opt/splunk/etc
      - splunk-var:/opt/splunk/var
      # ... existing mounts ...

volumes:
  splunk-etc:
  splunk-var:
```

## 5.3 The `honeynet_inputs` app

Rather than clicking data inputs into the GUI — which are then lost with the
container — ingestion is declared as a Splunk app that ships with the deployment.
This is the single best design decision in the stack.

```
splunk_apps/honeynet_inputs/
├── default/
│   └── app.conf
└── local/
    ├── inputs.conf
    └── props.conf
```

Mounted at `/opt/splunk/etc/apps/honeynet_inputs`. Splunk discovers it at
startup.

### `default/app.conf`

```ini
[install]
state = enabled

[package]
id = honeynet_inputs

[ui]
is_visible = 0
label = Honeynet Inputs
```

`is_visible = 0` hides it from the app menu — it has no UI, it is pure
configuration.

### `local/inputs.conf` — what gets indexed

```ini
[monitor:///data/cowrie/log/cowrie.json]
disabled = 0
index = main
sourcetype = cowrie:json
source = cowrie_json

[monitor:///data/cowrie/log/cowrie.log]
disabled = 0
index = main
sourcetype = cowrie:log
source = cowrie_text_log

[monitor:///data/dionaea/log/dionaea]
disabled = 0
index = main
sourcetype = dionaea:log
source = dionaea_log
recursive = true
```

Each stanza is a **file monitor**: Splunk tails the file and indexes new lines as
they appear, tracking its position so a restart does not re-index everything.

Paths are *container-internal*, mapping back through the volume mounts:

| Splunk sees | Host path | Written by |
|---|---|---|
| `/data/cowrie/log/cowrie.json` | `~/honeynet/cowrie-logs/cowrie.json` | the log bridge (doc 06) |
| `/data/dionaea/log/dionaea*` | `~/honeynet/dionaea-logs/` | Dionaea directly |

> The second stanza monitors `cowrie.log`, Cowrie's human-readable text log. That
> file is **not** produced by the current deployment — the bridge only carries
> `cowrie.json`. The stanza is harmless (Splunk waits for a file that never
> appears) but it is dead configuration.

> The third stanza's path, `/data/dionaea/log/dionaea`, has no extension and
> `recursive = true`. It matches the directory and picks up both `dionaea.log`
> and `dionaea-errors.log`.

### `local/props.conf` — how it gets parsed

```ini
[cowrie:json]
SHOULD_LINEMERGE = false
KV_MODE = json
TRUNCATE = 20000
TIME_PREFIX = "timestamp":\s*"
TIME_FORMAT = %Y-%m-%dT%H:%M:%S.%6N%z

[cowrie:log]
SHOULD_LINEMERGE = false
TRUNCATE = 20000

[dionaea:log]
SHOULD_LINEMERGE = false
TRUNCATE = 20000
```

Line by line, because each setting is doing real work:

**`SHOULD_LINEMERGE = false`** — treat every line as its own event. Without this,
Splunk applies heuristics to glue lines into multi-line events and will happily
merge unrelated JSON records.

**`KV_MODE = json`** — the setting that makes the dashboard possible. Splunk
parses each event as JSON and exposes every key as a searchable field, so
`src_ip`, `eventid`, `username`, `password`, `input`, `session` and `shasum` are
all directly queryable. Without it you would be writing `rex` extractions for
everything.

**`TRUNCATE = 20000`** — raise the default 10,000-byte event cap. Cowrie events
that embed a captured file's contents or a long command line can exceed the
default and get silently cut.

**`TIME_PREFIX` + `TIME_FORMAT`** — pin event time to Cowrie's own `timestamp`
field rather than letting Splunk guess. `%6N` is microseconds; `%z` is the
timezone offset. Cowrie emits e.g. `2026-08-07T09:16:35.751743Z`.

This matters more than it sounds. Without it, Splunk timestamps events by *index
time* — so after a restart, three months of backlogged log lines all arrive with
today's timestamp, and every time-based panel becomes a single meaningless spike.

## 5.4 Verifying ingestion

```bash
# Is the app loaded?
sudo docker exec splunk_dashboard ls /opt/splunk/etc/apps/ | grep honeynet

# Are the config files where Splunk expects them?
sudo docker exec splunk_dashboard ls -la /opt/splunk/etc/apps/honeynet_inputs/local

# Can Splunk see the log files through the mounts?
sudo docker exec splunk_dashboard ls -la /data/cowrie/log /data/dionaea/log

# What is Splunk actively monitoring?
sudo docker exec -it splunk_dashboard \
  /opt/splunk/bin/splunk list monitor -auth admin:'<PASSWORD>'
```

Then in Splunk Web:

```spl
index=main sourcetype=cowrie:json | head 20
```

```spl
index=main | stats count by sourcetype
```

If the second returns nothing, work backwards: is the bridge running
(`systemctl status honeynet-sync`), is the host file growing (`ls -lh
~/honeynet/cowrie-logs/cowrie.json`), can the container see it (`docker exec
splunk_dashboard ls -la /data/cowrie/log`)? Full procedure in
[doc 07](07-operations-runbook.md).

## 5.5 The dashboard — "Project Zeus: Executive Security Command"

Source: `config/splunk/dashboards/mousas_honeynet.xml`. Simple XML, dark theme,
seven panels.

### Importing it

1. Splunk Web → **Search & Reporting** → **Dashboards** → **Create New Dashboard**
2. Name it `mousas_honeynet` (this becomes the URL slug)
3. Pick **Classic Dashboards**, create, then **Edit → Source**
4. Replace the contents with the XML from this repo, save

Live at `http://<VM_PUBLIC_IP>:8000/en-US/app/search/mousas_honeynet`.

### Note on the defensive `rex` extractions

Several panels do this:

```spl
| rex field=_raw "\"src_ip\":\s*\"(?<raw_ip>[^\"]+)\""
| eval src_ip=coalesce(src_ip, raw_ip)
```

That is a belt-and-braces fallback: try the JSON-parsed field first, and if
`KV_MODE = json` did not apply for some reason, pull it out of the raw text with
a regex. It was added during a period when ingestion was misconfigured. It is now
redundant but harmless, and worth keeping as insurance.

---

### Panel 1 — Total Forensic Events

```spl
index=main sourcetype="cowrie:json" | stats count
```
*Time range: last 24 hours.* Single value, colour-banded: green below 50, amber
50–150, red above 150.

### Panel 2 — Unique Threat Actors

```spl
index=main sourcetype="cowrie:json" | stats dc(src_ip)
```
`dc()` = distinct count. Distinguishes "one persistent attacker" from "a botnet".

### Panel 3 — CRITICAL: Successful Breaches

```spl
index=main sourcetype="cowrie:json" eventid=cowrie.login.success | stats count
```
Green at zero, red at anything above. The headline number.

### Panel 4 — Attack Activity Timeline

```spl
index=main sourcetype="cowrie:json"
| eval Action=case(
    eventid=="cowrie.login.success",        "Login Success",
    eventid=="cowrie.command.input",        "Command Executed",
    eventid=="cowrie.session.file_download","Malware Download",
    eventid=="cowrie.login.failed",         "Brute Force Attempt")
| where isnotnull(Action)
| timechart span=1d count by Action
```
Stacked column chart. `case()` maps raw event IDs to analyst-readable labels;
`where isnotnull(Action)` drops the protocol chatter (`session.connect`,
`client.kex`) that would otherwise dominate.

### Panel 5 — Top 10 Guessed Passwords

```spl
index=main sourcetype="cowrie:json" eventid=cowrie.login.failed
| rex field=_raw "\"password\":\s*\"(?<raw_pass>[^\"]+)\""
| eval password=coalesce(password, raw_pass)
| top limit=10 password
```
Horizontal bar chart. Direct evidence of which credential lists are circulating.

### Panel 6 — Active Breach Details

```spl
index=main sourcetype="cowrie:json" eventid=cowrie.login.success
| rex field=_raw "\"src_ip\":\s*\"(?<raw_ip>[^\"]+)\""
| rex field=_raw "\"username\":\s*\"(?<raw_user>[^\"]+)\""
| rex field=_raw "\"password\":\s*\"(?<raw_pass>[^\"]+)\""
| eval src_ip=coalesce(src_ip, raw_ip)
| eval username=coalesce(username, raw_user)
| eval password=coalesce(password, raw_pass)
| iplocation src_ip
| table _time, src_ip, City, Country, username, password
| rename src_ip AS "Attacker", username AS "Target User", password AS "Used Password"
| sort - _time
```
`iplocation` is a built-in Splunk command backed by a bundled MaxMind GeoLite
database. No API key, no external call. It adds `City`, `Country`, `Region`,
`lat`, `lon`.

### Panel 7 — Global Threat Map

```spl
index=main sourcetype="cowrie:json" | iplocation src_ip | geostats count by Country
```
Cluster map. `geostats` aggregates by geographic bucket at the zoom level being
displayed.

> **Say this out loud when presenting:** *"This is IP geolocation. It resolves to
> the ISP's registered city or region, not a street address, and a VPN or proxy
> moves the pin entirely."* Overclaiming here is the fastest way to lose a
> technical audience.

### Panel 8 — Live Attack Narrative

```spl
index=main sourcetype="cowrie:json"
| rex field=_raw "\"src_ip\":\s*\"(?<raw_ip>[^\"]+)\""
| eval src_ip=coalesce(src_ip, raw_ip)
| eventstats values(src_ip) as src_ip by session
| eval Action=case(
    eventid=="cowrie.login.success",        "🔓 LOGIN SUCCESS",
    eventid=="cowrie.command.input",        "⌨️ COMMAND EXECUTED",
    eventid=="cowrie.session.file_download","📦 MALWARE DOWNLOADED",
    eventid=="cowrie.login.failed",         "🚫 BRUTE FORCE")
| where isnotnull(Action)
| eval Details = coalesce(input, url, "User: ".username." | Pass: ".password)
| eval Details = trim(Details)
| where Details!=""
| table _time, src_ip, Action, Details
| sort - _time
| rename src_ip AS "Source IP", Details AS "Activity Details"
```

The best panel on the dashboard, and worth understanding in detail:

- **`eventstats values(src_ip) as src_ip by session`** — Cowrie stamps `src_ip`
  on the connect event but not on every subsequent command event. This
  back-fills the IP across all events sharing a `session` ID, so command rows
  are attributable. Without it half the table has a blank Source IP.
- **`coalesce(input, url, ...)`** — one `Details` column that means "the command"
  for command events, "the URL" for downloads, and "user | pass" for logins.
- Emoji prefixes make severity scannable at a glance from across a room.

### Panel 9 — Signature Intelligence (VirusTotal)

```spl
index=main sourcetype="cowrie:json" eventid=cowrie.session.file_download
| table _time, src_ip, url, shasum
| rename shasum AS "SHA256_Hash"
```

With a **drilldown** that turns every hash into a live VirusTotal lookup:

```xml
<drilldown>
  <condition field="SHA256_Hash">
    <link target="_blank">https://www.virustotal.com/gui/search/$row.SHA256_Hash$</link>
  </condition>
</drilldown>
```

Click a hash → VirusTotal opens in a new tab with that hash pre-searched. This is
the "threat intelligence integration" requirement satisfied without an API key,
without rate limits, and without shipping data to a third party until an analyst
deliberately clicks.

## 5.6 Additional useful searches

Not on the dashboard, but worth having.

**Recent connections**
```spl
index=main sourcetype=cowrie:json eventid="cowrie.session.connect"
| table _time src_ip src_port dst_ip dst_port session
| sort - _time
```

**Top attacker IPs**
```spl
index=main sourcetype=cowrie:json src_ip=*
| stats count by src_ip
| sort - count
```

**All commands, ranked**
```spl
index=main sourcetype=cowrie:json eventid="cowrie.command.input"
| stats count by input
| sort - count
```

**Session timeline — one row per intrusion**
```spl
index=main sourcetype=cowrie:json session=*
| stats min(_time) as first_seen max(_time) as last_seen
        values(eventid) as events values(input) as commands
        by session src_ip
| convert ctime(first_seen) ctime(last_seen)
| sort - first_seen
```

**File transfers**
```spl
index=main sourcetype=cowrie:json
       eventid IN ("cowrie.session.file_download","cowrie.session.file_upload")
| table _time src_ip session eventid url filename outfile shasum
| sort - _time
```

**SSH client fingerprints — identifies tooling across IP rotation**
```spl
index=main sourcetype=cowrie:json eventid="cowrie.client.version"
| stats count dc(src_ip) as unique_ips by version
| sort - count
```

**Credential pairs, ranked**
```spl
index=main sourcetype=cowrie:json
       eventid IN ("cowrie.login.failed","cowrie.login.success")
| stats count by eventid username password
| sort - count
```

**Honeytoken tripwire — the highest-signal alert you can build**
```spl
index=main sourcetype=cowrie:json eventid="cowrie.command.input"
       input="*Project_Zeus*"
| table _time src_ip session input
```
Nobody legitimate ever touches that filename. Save it as an alert with a
real-time trigger and it will only ever fire on a genuine intrusion.

**Dionaea events**
```spl
index=main sourcetype=dionaea:log NOT "Cleanup"
| table _time _raw
| sort - _time
```

## 5.7 Splunk operations

```bash
# Health
sudo docker ps --filter name=splunk_dashboard --format '{{.Names}}\t{{.Status}}'
# want: "Up X hours (healthy)"

# Follow the boot sequence
sudo docker logs -f splunk_dashboard

# Restart (state survives; the container layer persists across restart)
sudo docker restart splunk_dashboard

# Reachable?
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000    # expect 303

# Reload config without a restart
sudo docker exec -it splunk_dashboard \
  /opt/splunk/bin/splunk reload monitor -auth admin:'<PASSWORD>'
```

### Fixing permission errors after a manual file drop

If you copy files into the Splunk container as root, Splunk (running as the
`splunk` user) may not be able to read them:

```bash
sudo docker exec -u root splunk_dashboard \
  chown -R splunk:splunk /opt/splunk/var /opt/splunk/etc
sudo docker restart splunk_dashboard
```

### Backing up the dashboard — do this now

The container layer is not persisted. One command saves the work:

```bash
sudo docker exec -u root splunk_dashboard \
  cat /opt/splunk/etc/users/admin/search/local/data/ui/views/mousas_honeynet.xml \
  > ~/honeynet/mousas_honeynet.backup.xml
```

Note the path: the dashboard was created through the GUI, so it is saved as a
**private** object under `etc/users/admin/`, not in an app directory. Private
objects are easy to lose. Consider moving it into the `honeynet_inputs` app at
`default/data/ui/views/` so it ships with the deployment and version-controls
cleanly.

---

Next: [06 — Log pipeline](06-log-pipeline.md)
