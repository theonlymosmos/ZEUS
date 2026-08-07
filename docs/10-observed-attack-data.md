# 10 — Observed attack data

Real telemetry from the live deployment, analysed at documentation time. This is
not simulated traffic — it is what the public internet did to one Azure VM over
101 days.

**Collection window:** 2026-04-28 14:53 UTC → 2026-08-07 09:16 UTC
**Source:** `cowrie.json` + rotated archives, from the anonymous volume
**Method:** JSON parse of all 2,987 events

---

## 10.1 Headline numbers

| Metric | Value |
|---|---:|
| Total Cowrie events | **2,987** |
| Unique source IPs | **318** |
| Distinct sessions | **566** |
| Failed logins | 407 |
| **Successful logins** | **35** |
| Commands executed | 204 |
| File downloads | 22 |
| File uploads | 32 |
| Payloads on disk | 31 (16 MB) |
| TTY recordings | 19 |
| Collection period | 101 days |

Roughly **5.6 sessions a day** from **~3 new IPs a day**, entirely unsolicited.
The host was never advertised anywhere.

## 10.2 Event breakdown

| Count | Event ID | Meaning |
|---:|---|---|
| 566 | `cowrie.session.connect` | TCP connection established |
| 565 | `cowrie.session.closed` | Session ended |
| 529 | `cowrie.client.version` | SSH client banner |
| 514 | `cowrie.client.kex` | Key exchange / HASSH fingerprint |
| 407 | `cowrie.login.failed` | Credential guess rejected |
| 204 | `cowrie.command.input` | Command typed in the fake shell |
| 35 | `cowrie.login.success` | **Attacker got in** |
| 32 | `cowrie.session.file_upload` | File written into the honeypot |
| 25 | `cowrie.client.var` | Client environment variable |
| 23 | `cowrie.session.params` | Session parameters |
| 22 | `cowrie.session.file_download` | Payload fetched via wget/curl |
| 19 | `cowrie.command.failed` | Command not implemented by Cowrie |
| 18 | `cowrie.log.closed` | TTY recording finalised |
| 16 | `cowrie.client.size` | Terminal dimensions |
| 4 | `cowrie.direct-tcpip.request` | **SSH tunnelling attempt** |
| 4 | `cowrie.direct-tcpip.data` | Data through the attempted tunnel |
| 4 | `cowrie.direct-tcpip.ja4h` | JA4H fingerprint of tunnelled traffic |

### Two things worth noticing

**566 connections, 35 successful logins, 204 commands.** Most connections never
get as far as a login attempt — they are scanners fingerprinting the SSH banner
and moving on. Of the sessions that did log in, a minority ran any command at
all. That ratio is typical and it is why honeypots need volume.

**`cowrie.direct-tcpip.request` — 4 events.** These are attempts to use the
compromised host as a **SSH tunnel / proxy**, not to exploit it. This is a
distinct and commercially motivated attack class: the attacker does not care
about your server, they want an IP address to launder traffic through — for
credential stuffing, ad fraud, or scraping. Cowrie logs the intent and refuses
the forward. The JA4H fingerprints on that tunnelled traffic identify the client
software behind it.

## 10.3 Top source IPs

> **Final octet masked.** Addresses appear as `41.239.245.x`. The network and ASN
> — which is what carries the analytical meaning — are intact, but full addresses
> of what are mostly compromised third-party machines are not republished here.
> The unmasked values remain in the raw `cowrie.json` on the host.


| Events | Source IP | Note |
|---:|---|---|
| 204 | `41.239.245.x` | Egypt — the most active source by far |
| 77 | `130.12.180.x` | |
| 65 | `156.216.147.x` | Egypt |
| 58 | `45.148.10.x` | Known scanning-infrastructure range |
| 52 | `41.239.147.x` | Egypt |
| 47 | `164.90.166.x` | DigitalOcean |
| 47 | `118.178.225.x` | Alibaba Cloud, China |
| 44 | `125.209.88.x` | |
| 43 | `45.100.61.x` | |
| 35 | `41.188.181.x` | |
| 35 | `114.31.27.x` | |
| 35 | `36.93.21.x` | Indonesia |
| 31 | `122.226.145.x` | China |
| 25 | `183.6.142.x` | China |
| 25 | `172.93.106.x` | |

The Egyptian addresses (`41.239.x`, `156.216.x`) are almost certainly the
project's own red-team testing, given the timing and command patterns. The rest
is genuine opportunistic scanning: cloud provider ranges (DigitalOcean, Alibaba),
Chinese and Indonesian consumer broadband, and dedicated scanning infrastructure.

The distribution has a long tail — 318 unique IPs across 566 sessions means most
sources connected once or twice. That is the signature of distributed botnet
scanning rather than targeted attention.

> **Privacy note.** These are real addresses belonging to real (mostly
> compromised) machines. Publishing honeypot IP data is standard practice in
> security research, but if you are publishing under a regime where IPs count as
> personal data, redact this table.

## 10.4 Successful logins

All 35 successes were against `root`:

| Password | Successes |
|---|---:|
| `admin` | 13 |
| `P` | 10 |
| *(empty string)* | 7 |
| `------fuck------` | 1 |
| `12321321` | 1 |
| `admun` | 1 |
| `szNIvgxXJs` | 1 |
| `password` | 1 |

### What this reveals about the credential policy

Cowrie is running its **built-in default database** (see
[F-01](09-findings-and-fixes.md)), which allows `root` with any password *except*
`root`, `123456`, and anything matching `/honeypot/i`.

That explains the shape of this table exactly:

- **`123456` never appears** — it is explicitly denied. Any demo script using
  `root`/`123456` will fail.
- **`P` (10 successes) and the empty string (7)** are not passwords anyone would
  guess. They are artifacts of automated clients sending truncated or empty
  credentials, which the permissive `root:x:*` rule happily accepts.
- **`szNIvgxXJs`** is a randomly-generated string — a bot testing whether the
  host accepts *anything*, which is a standard honeypot-detection probe. It got
  in, which told the bot exactly what it wanted to know.
- **`------fuck------`** is a signature from a known brute-force toolkit.

## 10.5 Failed logins

| Attempts | Username | Password |
|---:|---|---|
| 11 | `admin` | `admin` |
| 9 | `orangepi` | `orangepi` |
| 3 | `azureuser` | `Passw0rd12345` |
| 3 | `azureuser` | `Pa$$word1234` |
| 3 | `azureuser` | `p@ssw0rd@123` |
| 3 | `azureuser` | `Qwerty10` |
| 3 | `azureuser` | `Pa55word&` |
| 3 | `azureuser` | `Passw0rd!@#` |
| 2 | `azureuser` | `Azureuser@123` |
| 2 | `azureuser` | `!QAZ2wsx#EDC` |
| 2 | `azureuser` | `Azure123456!` |
| 2 | `azureuser` | `Qwertyuiop123` |
| 2 | `azureuser` | `P@$$w0rd1234` |
| 2 | `azureuser` | `password@12345` |
| 2 | `azureuser` | `Welcome@12345` |

### The `azureuser` campaign is the most interesting finding here

Attackers are running a wordlist **specifically targeting Azure's default admin
username**, with passwords crafted to satisfy Azure's complexity policy — mixed
case, digits, symbols, 12+ characters (`Azureuser@123`, `Azure123456!`,
`Passw0rd!@#`).

This is not generic scanning. Someone built a list for Azure VMs, knowing that
`azureuser` is the portal default and that a lazy admin under complexity pressure
picks something like `Passw0rd12345`.

**Operational consequence:** the real SSH port 22 on this host uses the username
`azureuser`. If port 22 were open to `Any` instead of a restricted source, this
exact wordlist would be running against the real admin account. Keep it locked to
your IP ([doc 02 §2.3](02-azure-and-host-setup.md)).

**`orangepi`/`orangepi`** (9 attempts) is IoT botnet behaviour — Orange Pi single
board computers ship with that default and are a favourite Mirai-variant target.

## 10.6 Commands executed

| Count | Command |
|---:|---|
| 38 | `ls` |
| 13 | `cd ..` |
| 11 | `cd /home` |
| 10 | `pwd` |
| 7 | `whoami` |
| 6 | `cat Project_Zeus_Master_DB_Backup.sql` |
| 6 | `./malware.sh` |
| 6 | `history -c` |
| 5 | `chmod +x clean.sh; sh clean.sh; rm -rf clean.sh; chmod +x setup.sh; sh setup.sh; rm -rf setup.sh; mkdir -p ~/.ssh; chatt…` |
| 5 | `cd phil` |
| 5 | `chmod +x malware.sh` |
| 3 | `uname -a` |
| 3 | `cd /home/phil` |
| 3 | `rm malware.sh` |
| 3 | `cat > malware.sh << 'EOF'` |
| 3 | `wget https://wildfire.paloaltonetworks.com/publicapi/test/pe -O wildfire_test.exe` |
| 2 | `ls -la /home/phil` |
| 2 | `wget https://secure.eicar.org/eicar.com` |
| 2 | `wget http://www.amtso.org/check-desktop-pua/ -O amtso_pua_test.exe` |
| 2 | `tar -cf loot.tar notes.txt` |
| 2 | `:(){ :\|:&};:}` |
| 1 | `cat /etc/passwd` |
| 1 | `cat /etc/shadow` |

### Reading the mix

Three distinct behaviour patterns are visible.

**Human reconnaissance** — `ls`, `pwd`, `whoami`, `cd ..`, `uname -a`. The
classic orientation sequence. Bots rarely bother.

**The honeytoken worked as designed.** `cd /home` (11), `cd phil` (5), `cat
Project_Zeus_Master_DB_Backup.sql` (6), `tar -cf loot.tar notes.txt` (2). The
bait drew attention exactly where intended, and someone tried to *package the
loot for exfiltration*. That `tar` command is the single clearest hostile-intent
signal in the dataset.

*(All six `cat` attempts returned nothing, because of
[F-01](09-findings-and-fixes.md). The intent is logged regardless — which is the
point.)*

**Real automated malware deployment** — this line, truncated in the table:

```
chmod +x clean.sh; sh clean.sh; rm -rf clean.sh; chmod +x setup.sh; sh setup.sh; rm -rf setup.sh; mkdir -p ~/.ssh; chatt…
```

Run 5 times. A textbook botnet install chain:
1. `clean.sh` — remove competing malware (botnets evict each other)
2. `setup.sh` — install the payload
3. `rm -rf` both — delete the evidence
4. `mkdir -p ~/.ssh` — prepare to plant an authorized key for persistence
5. `chattr` (truncated) — set the immutable bit so cleanup tools cannot remove it

**`:(){ :|:&};:}`** (2) is a **fork bomb**. On a real host it exhausts the
process table and hangs the machine. Cowrie's emulated shell simply logged it. Two
attackers tried to destroy a server that does not exist.

**`history -c`** (6) — attempts to erase shell history. Ineffective by design:
the logging happens in Cowrie's Python layer, entirely outside anything the
"shell" user controls. It is also, ironically, one of the strongest
intent-to-conceal signals available.

**`cat /etc/shadow`** — direct attempt at password hashes.

## 10.7 Payloads

22 download events, 31 files on disk (16 MB — the difference is upload events and
shell-redirect captures).

| Count | URL |
|---:|---|
| 3 | `https://wildfire.paloaltonetworks.com/publicapi/test/pe` |
| 2 | `https://secure.eicar.org/eicar.com` |
| 2 | `https://secure.eicar.org/eicar_com.zip` |
| 2 | `http://www.amtso.org/check-desktop-pua/` |
| 1 | `https://secure.eicar.org/eicar.com.txt` |
| 1 | `http://www.google.com` |

These particular URLs are **test files, from the project's own red-team
exercises** — EICAR is the standard AV test string, WildFire and AMTSO are Palo
Alto's and the Anti-Malware Testing Standards Organization's deliberately-benign
test samples. Ideal for demos: universally flagged by AV, completely inert.

The remaining files on disk have no recorded URL — they arrived through shell
redirects (`cat > malware.sh << 'EOF'`) or `file_upload` events. Several are
1.7–1.9 MB binaries with restrictive `0600` permissions, consistent with real
botnet payloads from the automated install chain in §10.6.

Files named `<timestamp>-<session>-redir__<path>` are redirect captures — e.g.
`20260428-191002-87f358ee01d3-0-redir__malware_sh`, the attacker writing
`malware.sh` by heredoc.

> 🚨 **Assume every file in `downloads/` is live malware.** Do not execute. Do not
> copy to a Windows host without AV. Do not commit — `.gitignore` blocks the
> directory.

### Hashing for threat intel

```bash
V=$(sudo docker inspect cowrie_honeypot \
      --format='{{range .Mounts}}{{if eq .Destination "/cowrie/cowrie-git/var"}}{{.Source}}{{end}}{{end}}')
sudo sh -c "find $V/lib/cowrie/downloads -type f -exec sha256sum {} \;"
```

Filenames are already SHA-256, so `https://www.virustotal.com/gui/search/<filename>`
works directly.

## 10.8 What this data demonstrates

For a project write-up, these are the defensible conclusions:

1. **An unadvertised cloud host is under continuous attack.** 318 unique sources
   in 101 days, with zero advertising. Exposure alone is sufficient.

2. **Default credentials are still the primary attack vector.** `admin/admin`,
   `orangepi/orangepi`, empty passwords. Not exploits — guesses.

3. **Attackers know cloud provider defaults.** The `azureuser` wordlist, tuned to
   Azure's complexity policy, is targeting rather than scanning.

4. **Compromised hosts have immediate resale value.** Four `direct-tcpip`
   tunnelling attempts show attackers monetising the IP address itself, without
   caring what the server does.

5. **Automated malware is competitive.** The `clean.sh` → `setup.sh` chain
   explicitly removes rival malware before installing its own.

6. **Honeytokens work.** The bait file drew repeated, targeted attention and one
   explicit exfiltration attempt (`tar -cf loot.tar`). Zero false positives — no
   legitimate process has any reason to touch it.

7. **Anti-forensics is reflexive and useless here.** Six `history -c` calls
   against a system where history was never the log source.

## 10.9 Reproducing this analysis

```bash
V=$(sudo docker inspect cowrie_honeypot \
      --format='{{range .Mounts}}{{if eq .Destination "/cowrie/cowrie-git/var"}}{{.Source}}{{end}}{{end}}')

sudo sh -c "cat $V/log/cowrie/cowrie.json*" | python3 -c "
import json, collections, sys
ev=collections.Counter(); ips=collections.Counter()
succ=collections.Counter(); fail=collections.Counter()
cmds=collections.Counter(); urls=collections.Counter()
sess=set(); first=last=None; n=0

for line in sys.stdin:
    try: d=json.loads(line)
    except: continue
    n+=1
    e=d.get('eventid'); ev[e]+=1
    ts=d.get('timestamp')
    if ts:
        first = ts if first is None or ts<first else first
        last  = ts if last  is None or ts>last  else last
    if d.get('session'): sess.add(d['session'])
    if d.get('src_ip'):  ips[d['src_ip']]+=1
    if e=='cowrie.login.success': succ[(d.get('username'),d.get('password'))]+=1
    if e=='cowrie.login.failed':  fail[(d.get('username'),d.get('password'))]+=1
    if e=='cowrie.command.input': cmds[d.get('input')]+=1
    if e=='cowrie.session.file_download': urls[d.get('url')]+=1

print('EVENTS:', n, '| IPs:', len(ips), '| SESSIONS:', len(sess))
print('WINDOW:', first, '->', last)
for title, c, k in [('EVENT TYPES',ev,None), ('TOP IPs',ips,15),
                    ('LOGIN SUCCESS',succ,15), ('LOGIN FAILED',fail,15),
                    ('COMMANDS',cmds,30), ('DOWNLOAD URLs',urls,12)]:
    print('\n---', title, '---')
    for key,v in c.most_common(k):
        if key is not None: print('  %6d  %s' % (v, str(key)[:110]))
"
```

Equivalent SPL, if you prefer Splunk:

```spl
index=main sourcetype=cowrie:json
| stats count               as events
        dc(src_ip)          as unique_ips
        dc(session)         as sessions
        min(_time)          as first_seen
        max(_time)          as last_seen
| convert ctime(first_seen) ctime(last_seen)
```

---

Back to the [README](../README.md).
