# 08 — Demo playbook (red team vs blue team)

A live two-role demonstration: one person attacks the honeynet, the other tracks
them in Splunk in real time. Runs in 12–15 minutes.

> **Authorization.** Only attack infrastructure you own or have written
> permission to test. This playbook assumes the target VM is yours.

## 8.1 Pre-flight — 30 minutes before

Run every check. Do not skip because "it worked yesterday" — the public IP moves.

```bash
# 1. Confirm the current public IP (it changes on deallocate/restart)
#    Azure portal > VM > Overview, or:
az vm list-ip-addresses -g rg-honeynet-demo -n mousahoneypot -o table

# 2. SSH in
ssh -i mousahoneypot_key.pem azureuser@<VM_PUBLIC_IP>

# 3. All three containers up
sudo docker ps --format 'table {{.Names}}\t{{.Status}}'

# 4. Splunk healthy AND serving
curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://localhost:8000   # want 303

# 5. Bridge alive, exactly one tail
sudo systemctl is-active honeynet-sync.service
ps aux | grep '[t]ail -F' | grep -c cowrie                            # want 1

# 6. Log flowing
ls -lh ~/honeynet/cowrie-logs/cowrie.json
```

**From the attack machine:**

```bash
nmap -Pn -p 21,2121,2222,2223,8000 <VM_PUBLIC_IP>
```

All five should be `open`. If `21` is `filtered`, your network blocks outbound
FTP — plan to use `2121`, or drop the FTP segment.

**Log into Splunk in a browser and load the dashboard** at
`http://<VM_PUBLIC_IP>:8000/en-US/app/search/mousas_honeynet`. Set the time picker
to **Last 15 minutes** and leave the tab open. Splunk's first login of the day is
slow; do not discover that on stage.

### Rehearse the login

```bash
ssh -o StrictHostKeyChecking=no -p 2222 root@<VM_PUBLIC_IP>
# password: admin
```

**Use `admin`, not `123456`.** The built-in credential database explicitly denies
`root`/`123456` (see [doc 03 §3.4](03-cowrie-config.md)). Older project notes are
wrong on this point and it will fail live.

### Know what will and will not work

| Demo beat | Status |
|---|---|
| SSH login to the trap | ✅ Works with `root`/`admin` |
| `ls /home/phil` showing honeytoken filenames | ✅ Works — served from the pickle |
| `cat Project_Zeus_Master_DB_Backup.sql` showing fake creds | ❌ **Returns empty** — see F-01 |
| `wget` payload capture + SHA-256 | ✅ Works — this is the reliable payload path |
| Splunk command/IP/geolocation panels | ✅ Works |
| TTY replay | ✅ Works |
| Dionaea FTP upload capture | ⚠️ Unreliable — passive-mode NAT issues |

Two options for the `cat` gap:

1. **Fix it first** — apply [F-01](09-findings-and-fixes.md), ~15 minutes.
2. **Reframe it honestly** — "the attacker enumerated the bait file; our
   detection fired on the *attempt*, which is the signal that matters." True, and
   it lands better than a panicked recovery.

## 8.2 Roles

**Red team** — a Kali VM (or any Linux/macOS shell) on a *different* network from
the VM, so geolocation shows a real distinct origin. Screen-shared.

**Blue team** — a browser on the Splunk dashboard, plus an SSH session to the VM
for the replay. Screen-shared side by side with red if possible.

## 8.3 Act 1 — Blue team framing (60 seconds)

> "What you are about to see is not our production server. It is a honeynet — a
> system built to be broken into. Every service on it is fake. There is no real
> data behind it and nothing to lose.
>
> What it does have is instrumentation. Cowrie emulates an SSH server and records
> every keystroke. Dionaea emulates vulnerable network services and quarantines
> anything uploaded. Both feed Splunk, which is what you see on the right.
>
> My colleague has not seen this dashboard. He is going to attack the host, and
> we are going to watch him do it."

Show the dashboard with all panels at or near zero. That baseline is what makes
the next five minutes land.

## 8.4 Act 2 — Reconnaissance (90 seconds)

**Red:**

```bash
nmap -Pn -p 21,2121,2222,2223,8000 <VM_PUBLIC_IP>
```

Expected:

```
PORT     STATE SERVICE
21/tcp   open  ftp
2121/tcp open  ccproxy-ftp
2222/tcp open  EtherNetIP-1
2223/tcp open  rockwell-csp2
8000/tcp open  http-alt
```

**Red explains the `-Pn`:**

> "`-Pn` skips host discovery. Azure drops ICMP by default, so a normal Nmap scan
> concludes the host is down and never touches a port. Cloud targets almost
> always need this flag."

> "Port 2222 is SSH on a non-standard port. That is a common 'security through
> obscurity' move by admins who think moving the port hides it. It does not — and
> in this case it is exactly the door I want."

**Blue:** note that recon alone produced almost no honeypot telemetry. A TCP
connect scan is nearly silent. Detection starts at the login attempt.

## 8.5 Act 3 — Intrusion (2 minutes)

**Red:**

```bash
ssh -o StrictHostKeyChecking=no -p 2222 root@<VM_PUBLIC_IP>
# password: admin
```

Land in the shell. Enumerate deliberately, pausing between commands so blue can
narrate:

```bash
whoami
id
hostname
uname -a
pwd
ls -la
cat /etc/passwd
cd /home
ls -la
cd phil
ls -la                              # ← the honeytokens appear
cat notes.txt
cat .bash_history                   # ← the breadcrumb trail
cat deploy.sh
grep -i password Project_Zeus_Master_DB_Backup.sql
```

**Red narrates as they go:**

> "Root on `prod-app-01`. Ubuntu 22.04. Let me see who else lives here… there is
> a user `phil`… and phil has left a database backup in his home directory."

**Blue, simultaneously, refreshing the dashboard:**

> "There he is. `Successful Breaches` just went from zero to one. The `Active
> Breach Details` table has his source IP, the username and the password he used
> — in cleartext, because we control the authentication code.
>
> Watch the `Live Attack Narrative` panel. Every command he types appears here
> within seconds. He is at `whoami`… `ls -la`… and now he has found `/home/phil`."

**The honeytoken moment** — when red runs the `grep`:

> "That file is a honeytoken. It is bait. It has never been part of any real
> system and no legitimate user has any reason to open it. The moment anyone
> reads it, we know with near-certainty that the session is hostile. It is one of
> the highest-signal, lowest-noise detections you can build — no machine
> learning, no baselining, just a file nobody should ever touch."

*(As currently deployed the `grep` returns nothing — see §8.1. Own it: "he tried
to exfiltrate it; the read attempt is logged, and that attempt is the alert.")*

## 8.6 Act 4 — Payload delivery (2 minutes)

The reliable capture path. Still inside the Cowrie session:

```bash
wget https://secure.eicar.org/eicar.com
ls -la
```

EICAR is the industry-standard antivirus test file — a harmless 68-byte string
that every AV engine on earth flags as malicious by agreement. Perfect for a
demo: universally detected, completely inert.

**Blue:**

> "Cowrie did not actually run `wget`. It emulated it — fetched the file to the
> host, computed its SHA-256, filed it in quarantine, and handed the attacker a
> convincing fake success message. He believes he has staged his payload. He has
> handed us a sample."

On the VM:

```bash
V=$(sudo docker inspect cowrie_honeypot \
      --format='{{range .Mounts}}{{if eq .Destination "/cowrie/cowrie-git/var"}}{{.Source}}{{end}}{{end}}')
sudo sh -c "find $V/lib/cowrie/downloads -type f -newermt '-10 minutes' -exec sha256sum {} \;"
```

Then on the dashboard: the **Signature Intelligence** panel now has a row. **Click
the hash** — VirusTotal opens with it pre-searched, showing vendor detections.

> "That is the full intelligence loop. Attacker delivers a payload, we capture
> it, hash it, and enrich it against a global reputation service — without ever
> executing it. That hash now goes to our threat-intel platform, and every other
> host in the estate can block it."

If the demo needs a live scan of *arbitrary* attacker traffic, the palo alto
WildFire test file also works:

```bash
wget https://wildfire.paloaltonetworks.com/publicapi/test/pe -O test.exe
```

## 8.7 Act 5 — TTY replay, the closer (2 minutes)

Red exits:

```bash
history -c
exit
```

*(`history -c` is a nice beat — the attacker's attempt to cover tracks, which
does nothing, because the logging is not in the shell.)*

Blue, on the VM:

```bash
V=$(sudo docker inspect cowrie_honeypot \
      --format='{{range .Mounts}}{{if eq .Destination "/cowrie/cowrie-git/var"}}{{.Source}}{{end}}{{end}}')
sudo ls -lat $V/lib/cowrie/tty/ | head -5

sudo docker exec -it cowrie_honeypot \
  /cowrie/cowrie-env/bin/python3 -m cowrie.scripts.playlog /cowrie/cowrie-git/var/lib/cowrie/tty/<NEWEST_FILE>
```

The session replays with original keystroke timing — typos, backspaces, thinking
pauses and all.

> "He cleared his bash history on the way out. It made no difference. This is not
> a log of his session — it is a *recording* of it, captured at the terminal
> layer, outside anything he had control over. Every keystroke, at the speed he
> typed it."

This is the moment people remember. End on it.

## 8.8 Act 6 — Optional: Dionaea FTP (90 seconds)

Only if it was verified working during pre-flight. Skip without apology if not.

**Red:**

```bash
cat > demo_payload.sh <<'EOF'
#!/bin/sh
echo "BENIGN DEMO PAYLOAD - NOT REAL MALWARE"
uname -a
id
EOF
sha256sum demo_payload.sh

# Active mode avoids the passive-port NAT problem
lftp -u anonymous,anonymous \
  -e "set ftp:passive-mode no; put demo_payload.sh; bye" \
  ftp://<VM_PUBLIC_IP>:2121
```

**Blue:**

```bash
sudo docker exec dionaea_honeypot find /opt/dionaea/var/lib/dionaea -type f | tail -10
sudo tail -20 ~/honeynet/dionaea-logs/dionaea.log | grep -v Cleanup
```

If nothing is captured, say so plainly:

> "The FTP data channel did not establish — that is a NAT and passive-mode
> limitation, and it is genuinely common in the field. The control channel is
> recorded, so we still have the full command sequence and the source IP. Cowrie
> is our reliable capture path, and it already has 31 real samples."

**Never fake a capture.** One fabricated result destroys the credibility of
everything before it.

## 8.9 Act 7 — Close (60 seconds)

Return to the dashboard, now populated. Walk the panels top to bottom:

1. **Total Forensic Events** — volume
2. **Unique Threat Actors** — one attacker, or a botnet?
3. **Successful Breaches** — red
4. **Global Threat Map** — where they came from
5. **Live Attack Narrative** — the full story, in order

> "Ten minutes. One attacker. We have his IP, his geographic origin, his SSH
> client fingerprint, the exact credentials he guessed, every command he typed,
> a hash of the payload he tried to stage, and a video of his session.
>
> He got none of ours. Every file he found was fake, every credential he stole
> was invalid, and the server he thought he owned never existed.
>
> That asymmetry is the point of a honeypot. He has to be right every time. We
> only have to be watching."

### If the audience is technical, be ready for

**"Couldn't he tell it was a honeypot?"**
> Yes, a careful attacker could. Cowrie's emulated shell has tells — unusual
> command coverage, timing artifacts, a filesystem that does not behave under
> pressure. This is medium-interaction deception; it is designed to catch
> automated and opportunistic attacks, which are the overwhelming majority. A
> targeted adversary is a different problem needing a different tool.

**"Is the geolocation accurate?"**
> It resolves to the ISP's registered city or region, not an address. VPNs and
> proxies move the pin entirely. It is useful for pattern analysis — which
> regions and networks are probing us — not for attribution.

**"What if he escapes the container?"**
> The real risk, and why the host is isolated: no production resources on the
> VNet, no reusable credentials, no path back to anything real. The honeypot is
> already assumed compromised.

## 8.10 Evidence checklist

Screenshot these — before the demo, so you are not scrambling if something fails
live:

- [ ] `sudo docker ps` — all three containers up
- [ ] Nmap output showing the open ports
- [ ] Kali SSH session inside `/home/phil` with the honeytokens listed
- [ ] Splunk `cowrie.command.input` events
- [ ] Splunk geolocation cluster map
- [ ] Splunk **Active Breach Details** table with cleartext credentials
- [ ] TTY `playlog` replay mid-playback
- [ ] `sha256sum` output for a captured payload
- [ ] VirusTotal page for that hash
- [ ] Dionaea logs or captured bistreams (if FTP worked)

## 8.11 Reset between runs

```bash
# Narrow the Splunk time picker to "Last 15 minutes" for a clean-looking board
# (does not delete data — usually what you want)

# Full data wipe, ⚠️ destroys ALL collected telemetry including 3 months of
# real internet attack data. Think hard before running this.
cd ~/honeynet
sudo docker compose down -v
sudo docker compose up -d
sudo systemctl restart honeynet-sync.service
```

Back the data up first (see [doc 07 §7.8](07-operations-runbook.md)). The
collected dataset is more valuable than a clean dashboard.

---

Next: [09 — Findings & fixes](09-findings-and-fixes.md)
