# 01 — Architecture

## 1.1 Design intent

The honeynet answers three questions about anyone who touches it:

1. **Who are you?** — source IP, geolocation, SSH client fingerprint (HASSH).
2. **What did you try?** — every credential guessed, every command typed,
   recorded keystroke-by-keystroke.
3. **What did you bring?** — every file downloaded into the fake shell, hashed
   with SHA-256 and quarantined for threat-intel lookup.

Everything else is in service of those three questions.

The deception layer is deliberately *shallow but convincing*: an attacker who
lands in the Cowrie shell believes they are on `prod-app-01`, an Ubuntu 22.04
application server, on which a careless engineer named `phil` left a production
database dump in his home directory. There is no real filesystem behind it. The
"database dump" is bait — a **honeytoken** — and reading it is the loudest
possible signal that a session is hostile rather than accidental.

## 1.2 Component layout

```
                          INTERNET
                              │
                              │
              ┌───────────────┴────────────────┐
              │   Azure Network Security Group │
              │   inbound allow: 22 · 21 ·     │
              │   2121 · 2222 · 2223 · 8000    │
              └───────────────┬────────────────┘
                              │
╔═════════════════════════════╪══════════════════════════════════════╗
║  Azure VM  mousahoneypot    │   10.0.0.4/24                        ║
║  Ubuntu 24.04 · Docker CE   │                                      ║
║                             │                                      ║
║   ┌─────────────────────────┴──────────────────────────────────┐   ║
║   │   docker bridge network  honeynet_honeynet  172.18.0.0/16  │   ║
║   │                                                            │   ║
║   │  ┌──────────────────┐  ┌──────────────────┐                │   ║
║   │  │ cowrie_honeypot  │  │ dionaea_honeypot │                │   ║
║   │  │   172.18.0.4     │  │   172.18.0.2     │                │   ║
║   │  │                  │  │                  │                │   ║
║   │  │ :2222  SSH       │  │ :21   FTP        │                │   ║
║   │  │ :2223  Telnet    │  │ +15 more services│                │   ║
║   │  │                  │  │                  │                │   ║
║   │  │ hostname:        │  │ captures binaries│                │   ║
║   │  │  prod-app-01     │  │ + bistreams      │                │   ║
║   │  └────────┬─────────┘  └────────┬─────────┘                │   ║
║   │           │                     │                          │   ║
║   │           │ cowrie.json         │ dionaea.log              │   ║
║   │           │ (anonymous volume)  │ (bind mount)             │   ║
║   │           ▼                     ▼                          │   ║
║   │  ┌──────────────────────────────────────────┐              │   ║
║   │  │  honeynet-sync.service  (systemd, root)  │              │   ║
║   │  │  tail -F  →  ~/honeynet/cowrie-logs/     │              │   ║
║   │  │  the "log bridge" — see doc 06           │              │   ║
║   │  └───────────────────┬──────────────────────┘              │   ║
║   │                      │                                     │   ║
║   │                      ▼                                     │   ║
║   │           ┌────────────────────────┐                       │   ║
║   │           │   splunk_dashboard     │                       │   ║
║   │           │     172.18.0.3         │                       │   ║
║   │           │                        │                       │   ║
║   │           │  :8000  Splunk Web     │                       │   ║
║   │           │  app: honeynet_inputs  │                       │   ║
║   │           │  index=main            │                       │   ║
║   │           └────────────────────────┘                       │   ║
║   └────────────────────────────────────────────────────────────┘   ║
╚════════════════════════════════════════════════════════════════════╝
```

## 1.3 The three containers

### `cowrie_honeypot` — the deception sensor

Image `cowrie/cowrie:latest`, running Cowrie **2.9.17.dev1+gcd0770d3d** on
Python 3.13 / Twisted 25.5.0. Presents itself as `prod-app-01`.

Cowrie is a *medium-interaction* honeypot. It does not run a real shell — it
emulates one in Python. Commands like `ls`, `cd`, `cat`, `wget` are Python
reimplementations operating on a fake filesystem held in memory. This is what
makes it safe to expose: an attacker who types `rm -rf /` destroys nothing,
because there is nothing there.

What it records for every session:

- `cowrie.session.connect` — source IP/port, destination, session ID
- `cowrie.client.version` / `cowrie.client.kex` — SSH client banner and the
  **HASSH** fingerprint, which identifies the attacker's *tooling* even when
  their IP rotates
- `cowrie.login.failed` / `cowrie.login.success` — every username and password
  in cleartext
- `cowrie.command.input` — every command typed
- `cowrie.session.file_download` / `file_upload` — payloads, with SHA-256
- A **TTY replay log** per session: a byte-level recording of the terminal,
  replayable as video via `bin/playlog`

### `dionaea_honeypot` — the malware-capture sensor

Image `dinotools/dionaea:latest`. Where Cowrie emulates a *shell*, Dionaea
emulates *network services* and waits for exploitation attempts. Sixteen service
handlers are enabled (full list in [doc 04](04-dionaea-config.md)), of which FTP
is the one published to the internet.

Its job is to accept a malicious upload, refuse to run it, and file it in a
quarantine directory keyed by hash.

### `splunk_dashboard` — the SIEM

Image `splunk/splunk:latest`, Splunk Enterprise with a free-tier licence. Reads
both sensors' logs via read-only volume mounts and indexes them into `index=main`
under two sourcetypes: `cowrie:json` and `dionaea:log`.

The `honeynet_inputs` app makes ingestion declarative — the monitors are defined
in a config file that ships with the deployment, rather than clicked into the GUI
and lost on rebuild.

## 1.4 Data flow, end to end

```
attacker types  `cat Project_Zeus_Master_DB_Backup.sql`
      │
      ▼
Cowrie's Python shell intercepts the command
      │
      ├─ writes a JSON line to  var/log/cowrie/cowrie.json
      │     {"eventid":"cowrie.command.input","input":"cat Project_...",
      │      "src_ip":"41.239.245.x","session":"9360a67f1373", ...}
      │
      └─ appends raw terminal bytes to  var/lib/cowrie/tty/<sha256>
      │
      ▼
honeynet-sync.service  (tail -F)
      │  bridges the container's anonymous Docker volume
      │  to  /home/azureuser/honeynet/cowrie-logs/cowrie.json
      ▼
Docker read-only mount  →  /data/cowrie/log/cowrie.json  inside Splunk
      │
      ▼
inputs.conf  [monitor:///data/cowrie/log/cowrie.json]
      │  sourcetype = cowrie:json
      ▼
props.conf   KV_MODE = json   → every JSON key becomes a searchable field
      │
      ▼
index=main
      │
      ▼
Dashboard panel "Live Attack Narrative"
      │  | eval Action=case(eventid=="cowrie.command.input","⌨️ COMMAND EXECUTED", ...)
      ▼
Analyst sees: 41.239.245.x · ⌨️ COMMAND EXECUTED · cat Project_Zeus_Master_DB_Backup.sql
```

The single non-obvious hop is the `tail -F` bridge. It exists because of a
volume-path mismatch in the Compose file, explained in full in
[doc 06](06-log-pipeline.md) and [finding F-01](09-findings-and-fixes.md).

## 1.5 Network topology

| Interface | Address | Role |
|---|---|---|
| `eth0` | `10.0.0.4/24` | Azure VNet, subnet `10.0.0.0/24` |
| `br-aabec032d700` | `172.18.0.1/16` | Docker bridge for `honeynet_honeynet` |
| `docker0` | `172.17.0.1/16` | Docker default bridge (unused) |
| `br-264eb3ee2526` | `172.19.0.1/16` | Leftover bridge from an earlier stack |

Container addresses on `honeynet_honeynet`:

| Container | IP |
|---|---|
| `dionaea_honeypot` | `172.18.0.2` |
| `splunk_dashboard` | `172.18.0.3` |
| `cowrie_honeypot`  | `172.18.0.4` |

These are assigned by Docker in start order and **will shift on restart**. Do not
hardcode them. It matters for one thing: Dionaea logs record the *container's*
destination IP, so historical logs contain both `172.18.0.2` and `172.18.0.3` as
the FTP listener — an artifact of restarts, not two separate services.

## 1.6 Port mapping

| Host port | Container | Container port | Exposure | Notes |
|---:|---|---:|---|---|
| 22 | *(host sshd)* | 22 | **Restrict to your IP** | Real admin access |
| 21 | dionaea | 21 | Public | Primary FTP trap |
| 2121 | dionaea | 21 | Public | Same service, second port — ISP-filter workaround |
| 2222 | cowrie | 2222 | Public | SSH trap |
| 2223 | cowrie | 2223 | Public | Telnet trap |
| 8000 | splunk | 8000 | **Should be your IP only** | Splunk Web |

Note that `21` and `2121` both map to container port `21` — one Dionaea FTP
service reachable on two host ports. Residential ISPs and corporate egress
filters routinely block outbound TCP/21, which would silently kill the FTP half
of a live demo; `2121` is the escape hatch.

## 1.7 Persistence model

| Data | Where it lives | Survives `compose down`? | Survives `down -v`? |
|---|---|---|---|
| Cowrie JSON logs | anonymous volume `6e416b9f…` | Yes | **No** |
| Cowrie TTY replays | same anonymous volume | Yes | **No** |
| Cowrie payloads | same anonymous volume | Yes | **No** |
| Bridged log copy | `~/honeynet/cowrie-logs/` (host) | Yes | **Yes** |
| Dionaea logs | `~/honeynet/dionaea-logs/` (host bind) | Yes | **Yes** |
| Dionaea binaries | container layer | **No** | No |
| Splunk index | container layer | **No** | No |
| Splunk dashboard | container layer | **No** | No |

Two entries deserve alarm:

- **Splunk's index and dashboards are not persisted.** No named volume is mounted
  at `/opt/splunk/var` or `/opt/splunk/etc`. A `docker compose down` destroys
  every indexed event and the dashboard XML. See
  [finding F-03](09-findings-and-fixes.md). Back the dashboard up now:
  it is preserved in this repo at `config/splunk/dashboards/mousas_honeynet.xml`,
  but that snapshot only reflects the state at documentation time.
- **Dionaea's captured binaries are not persisted** either — only its logs are.

Cowrie's data is safe from `down` but not from `down -v`, and lives in a volume
with a random name, which is exactly what makes the log bridge necessary.

---

Next: [02 — Azure & host setup](02-azure-and-host-setup.md)
