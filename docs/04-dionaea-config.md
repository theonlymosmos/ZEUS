# 04 — Dionaea configuration (malware-capture honeypot)

## 4.1 What Dionaea is for

Cowrie catches people who log in. Dionaea catches people who never bother to log
in — the mass-scanning worms and exploit kits that fire payloads at open ports
and move on. It emulates the *server side* of a dozen protocols well enough to
complete a handshake, accept whatever the attacker sends, and write it to disk
without ever executing it.

Where Cowrie's output is a behavioural narrative, Dionaea's output is a pile of
binaries and a record of who sent them.

## 4.2 The Compose service

```yaml
  dionaea:
    image: dinotools/dionaea:latest
    container_name: dionaea_honeypot
    ports:
      - "21:21"
      - "2121:21"
    volumes:
      - ./dionaea-logs:/opt/dionaea/var/log/dionaea
    restart: always
    networks:
      - honeynet
```

Both host ports map to the same container port 21 — one FTP service, two front
doors. `2121` exists because residential ISPs and corporate egress filters
routinely block outbound TCP/21, which would silently kill an FTP demo from a
laptop on a home connection.

### Verified mount

```console
$ sudo docker inspect dionaea_honeypot --format '{{json .Mounts}}'
[
  {
    "Type": "bind",
    "Source": "/home/azureuser/honeynet/dionaea-logs",
    "Destination": "/opt/dionaea/var/log/dionaea",
    "Mode": "rw",
    "RW": true
  }
]
```

Exactly one mount, and it is correct — unlike Cowrie, Dionaea's paths line up.

**But only logs are persisted.** `/opt/dionaea/var/lib/dionaea`, which holds the
captured binaries and the connection streams, lives in the container's writable
layer. `docker compose down` destroys every sample collected. See
[finding F-03](09-findings-and-fixes.md).

## 4.3 Enabled services

Sixteen protocol handlers are active inside the container:

```console
$ sudo docker exec dionaea_honeypot ls /opt/dionaea/etc/dionaea/services-enabled/
blackhole.yaml   ftp.yaml     memcache.yaml   mongo.yaml   mssql.yaml
mysql.yaml       epmap.yaml   http.yaml       mirror.yaml  mqtt.yaml
pptp.yaml        printer.yaml sip.yaml        smb.yaml     tftp.yaml
upnp.yaml
```

| Service | Default port | What it baits |
|---|---:|---|
| `ftp` | 21 | Anonymous upload, malware staging |
| `smb` | 445 | EternalBlue-class exploits, worm propagation |
| `http` | 80 | Web shells, scanners |
| `mssql` | 1433 | SQL Server brute force, `xp_cmdshell` |
| `mysql` | 3306 | MySQL brute force |
| `sip` | 5060 | VoIP toll fraud |
| `mqtt` | 1883 | IoT command-and-control |
| `mongo` | 27017 | Unauthenticated Mongo ransom |
| `memcache` | 11211 | Amplification-DDoS reflectors |
| `tftp` | 69 | Router/IoT firmware drops |
| `upnp` | 1900 | Device discovery abuse |
| `epmap` | 135 | DCE/RPC endpoint mapper |
| `printer` | 9100 | Raw print-job abuse |
| `pptp` | 1723 | VPN probing |
| `mirror` | — | Echoes input back; probe fingerprinting |
| `blackhole` | — | Silently absorbs unmatched traffic |

**Only FTP is exposed.** The other fifteen listen inside the container but no
host port maps to them, so the internet cannot reach them. That is a deliberate
attack-surface decision: SMB in particular attracts enormous worm traffic that
would drown the interesting signal.

To expose more, add mappings *and* matching NSG rules:

```yaml
    ports:
      - "21:21"
      - "2121:21"
      - "445:445"      # SMB — expect a flood
      - "1433:1433"    # MSSQL
      - "3306:3306"    # MySQL
      - "80:80"        # HTTP
```

## 4.4 `dionaea.cfg` — the main configuration

Read live from `/opt/dionaea/etc/dionaea/dionaea.cfg`:

```ini
[dionaea]
download.dir=var/lib/dionaea/binaries/
#modules=curl,python,nfq,emu,pcap
modules=curl,python,emu
processors=filter_streamdumper,filter_emu

listen.mode=getifaddrs
# listen.addresses=127.0.0.1
# listen.interfaces=eth0,tap0

[logging]
default.filename=var/log/dionaea/dionaea.log
default.levels=all
default.domains=*

errors.filename=var/log/dionaea/dionaea-errors.log
errors.levels=warning,error
errors.domains=*

[processor.filter_emu]
name=filter
config.allow.0.protocols=smbd,epmapper,nfqmirrord,mssqld
next=emu

[processor.filter_streamdumper]
name=filter
config.allow.0.types=accept
config.allow.1.types=connect
config.allow.1.protocols=ftpctrl
config.deny.0.protocols=ftpdata,ftpdatacon,xmppclient
next=streamdumper
```

### Key settings explained

**`download.dir=var/lib/dionaea/binaries/`** — quarantine for captured payloads,
named by hash.

**`modules=curl,python,emu`** — `nfq` and `pcap` are commented out. `pcap` would
need `CAP_NET_RAW`, which the container does not have; `nfq` needs netfilter
queue access. Their absence is correct for a containerised deployment.

**`emu`** is libemu, a lightweight x86 emulator. When a payload arrives, Dionaea
*emulates* the shellcode to work out what it would have done — typically
revealing a download URL — without running it on real hardware. This is how
Dionaea turns an exploit attempt into an actionable IOC.

**`listen.mode=getifaddrs`** — bind to all interfaces the container can see.

**`default.levels=all`** — log everything. This is why `dionaea.log` grows fast
and is mostly noise; see §4.6.

**`filter_streamdumper` deny list** — `ftpdata` and `ftpdatacon` are excluded
from stream dumping so that bulk file transfers do not fill the disk. The control
channel (`ftpctrl`) *is* dumped, so you still get the commands.

## 4.5 Captured data

### Location

```console
$ sudo docker exec dionaea_honeypot find /opt/dionaea/var/lib/dionaea -type f
/opt/dionaea/var/lib/dionaea/sip/accounts.sqlite
/opt/dionaea/var/lib/dionaea/bistreams/2026-04-28/ftpd-172.18.0.3-21-101.36.109.x-26752-2026-04-28T15:30:28.083712-KSl15Q
/opt/dionaea/var/lib/dionaea/bistreams/2026-04-28/ftpd-172.18.0.2-21-41.239.245.x-53479-2026-04-28T19:37:28.025164-5oUACm
/opt/dionaea/var/lib/dionaea/bistreams/2026-04-28/ftpd-172.18.0.3-21-205.210.31.x-52658-...
/opt/dionaea/var/lib/dionaea/bistreams/2026-04-28/ftpd-172.18.0.3-21-3.143.162.x-55110-...
...
```

### Reading a bistream filename

```
ftpd - 172.18.0.3 - 21 - 205.210.31.x - 52658 - 2026-04-28T17:01:28 - 7jXTZP
 │        │         │          │            │              │              │
 │        │         │          │            │              │              └ random suffix
 │        │         │          │            │              └ timestamp
 │        │         │          │            └ attacker source port
 │        │         │          └ ATTACKER SOURCE IP
 │        │         └ destination port
 │        └ destination (container) IP
 └ protocol handler
```

A **bistream** is a bidirectional recording of the whole TCP conversation — both
what the attacker sent and what Dionaea replied. It is the FTP equivalent of
Cowrie's TTY replay.

Note both `172.18.0.2` and `172.18.0.3` appear as the destination. That is not
two services — it is the same container getting a different Docker-assigned IP
after a restart.

### Directory map

| Path | Contents |
|---|---|
| `var/lib/dionaea/binaries/` | Captured payloads, named by MD5 hash |
| `var/lib/dionaea/bistreams/<date>/` | Full TCP conversation dumps |
| `var/lib/dionaea/sip/accounts.sqlite` | SIP registration attempts |
| `var/log/dionaea/dionaea.log` | Everything (~2.1 MB) |
| `var/log/dionaea/dionaea-errors.log` | Warnings and errors only (~126 KB) |

### Hashing captures for threat intel

```bash
sudo docker exec dionaea_honeypot \
  find /opt/dionaea/var/lib/dionaea/binaries -type f -exec sha256sum {} \; 2>/dev/null
```

Paste any hash into `https://www.virustotal.com/gui/search/<hash>`.

## 4.6 The log-noise problem

`dionaea.log` is dominated by a housekeeping message from the SIP module:

```
[07082026 08:50:35] sip /dionaea/sip/__init__.py:45-warning: Cleanup
[07082026 08:51:35] sip /dionaea/sip/__init__.py:45-warning: Cleanup
[07082026 08:52:35] sip /dionaea/sip/__init__.py:45-warning: Cleanup
```

One line per minute, forever — roughly 1,440 lines a day of pure noise, and the
bulk of the 2.1 MB file. It is harmless but it dilutes the Splunk index and makes
`dionaea:log` searches unpleasant.

Three ways to deal with it, best first:

**1. Disable the SIP service** (nothing maps to it anyway):

```bash
sudo docker exec dionaea_honeypot \
  mv /opt/dionaea/etc/dionaea/services-enabled/sip.yaml \
     /opt/dionaea/etc/dionaea/services-available/
sudo docker restart dionaea_honeypot
```

**2. Raise the log level** in `dionaea.cfg`:

```ini
default.levels=info,warning,error   # instead of `all`
```

**3. Drop it at ingest** — add to `props.conf` / `transforms.conf`:

```ini
# props.conf
[dionaea:log]
TRANSFORMS-null = dionaea_sip_cleanup_nullqueue

# transforms.conf
[dionaea_sip_cleanup_nullqueue]
REGEX = sip /dionaea/sip/__init__\.py:\d+-warning: Cleanup
DEST_KEY = queue
FORMAT = nullQueue
```

Option 3 keeps the raw file intact on disk while keeping the index clean — the
right choice if you want the option of going back to the raw logs later.

## 4.7 Known limitation: FTP data-channel captures

FTP uses two connections: a control channel for commands, and a separate data
channel for the file itself. In passive mode the client asks the server to open a
second port and connect back — behind Docker's NAT and an Azure NSG, that second
connection frequently never establishes.

Result: you reliably see the *attempt* in the control-channel bistream, but the
uploaded file itself often never lands in `binaries/`.

Mitigations for a live demo:

```bash
# Force active mode, which avoids the passive port negotiation
lftp -u anonymous,anonymous \
  -e "set ftp:passive-mode no; put demo_payload.sh; bye" \
  ftp://<VM_PUBLIC_IP>:2121
```

And accept the honest fallback: **Cowrie's `wget`/`curl` capture is the reliable
payload path.** Cowrie has already captured 31 real payloads this way against
Dionaea's zero. If FTP fails on demo day, say so and pivot — do not fake a
capture.

## 4.8 Health checks

```bash
# Container up?
sudo docker ps --filter name=dionaea_honeypot --format '{{.Names}}\t{{.Status}}'

# Listening on both host ports?
sudo ss -tulpn | grep -E ':(21|2121)\b'

# Recent activity (skipping the SIP noise)
sudo grep -v 'sip.*Cleanup' ~/honeynet/dionaea-logs/dionaea.log | tail -30

# Errors only
sudo tail -50 ~/honeynet/dionaea-logs/dionaea-errors.log

# Everything captured so far
sudo docker exec dionaea_honeypot find /opt/dionaea/var/lib/dionaea -type f | sort
```

---

Next: [05 — Splunk configuration](05-splunk-config.md)
