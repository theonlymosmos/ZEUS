# Project Zeus — Azure Honeynet with Cowrie, Dionaea and Splunk

A multi-sensor honeynet running on a single Azure Ubuntu VM. Two deception
sensors (an SSH/Telnet honeypot and a malware-capture honeypot) feed a Splunk
Enterprise SIEM that renders attacker telemetry as a live threat-intelligence
dashboard — source IPs, geolocation, guessed credentials, executed commands,
and SHA-256 hashes of every payload the attacker pulled down.

The deployment has been exposed to the public internet continuously since
**2026-04-28**. It is not a lab simulation with synthetic traffic: the numbers
in [`docs/10-observed-attack-data.md`](docs/10-observed-attack-data.md) are real
opportunistic attacks against a real host.

> **Codename note.** "Project Zeus" is the *fictional* company the honeypot
> pretends to be. The bait files reference a `zeus_prod` database and a
> `Project_Zeus_Master_DB_Backup.sql` dump. None of it is real. That is the point.

---

## What this repository is

This is a **documentation and configuration repository**, reverse-engineered
from a live running deployment. Every config file under [`config/`](config/) was
pulled off the running VM verbatim, and every claim in the docs is backed by
output captured from that VM.

It is written so that someone who has never seen the deployment can rebuild it,
operate it, demo it, and understand where it is wired incorrectly.

---

## At a glance

| Property | Value |
|---|---|
| Host | Azure Ubuntu VM, `mousahoneypot` |
| Kernel | Linux 6.17.0-1011-azure (Ubuntu 24.04 base) |
| Public IP | `<VM_PUBLIC_IP>` — see note below |
| Private IP | `10.0.0.4/24` |
| Orchestration | Docker Compose V2, project name `honeynet` |
| Sensors | Cowrie 2.9.17 (SSH/Telnet), Dionaea (16 emulated services) |
| SIEM | Splunk Enterprise (Docker image `splunk/splunk:latest`) |
| Deployment root | `/home/azureuser/honeynet` |

> ### 📍 About `<VM_PUBLIC_IP>`
>
> Throughout these docs, `<VM_PUBLIC_IP>` stands in for the honeypot VM's public
> address. **Substitute your own** — every command is otherwise copy-paste ready.
>
> The real address is deliberately not published here. Two reasons: an Azure
> public IP allocated dynamically changes whenever the VM is deallocated (this
> one has already moved once during the project's life), so any hardcoded address
> would go stale; and naming a live honeypot's address alongside its exact
> configuration lets anyone fingerprint it as a honeypot, which is precisely what
> a deception sensor must avoid.
>
> To find yours: **Azure portal → Virtual machines → *your VM* → Overview →
> Public IP address**, or `az vm list-ip-addresses -g <rg> -n <vm> -o table`.
> See [doc 02 §2.1](docs/02-azure-and-host-setup.md) for making it static.

### Service endpoints

| Service | Endpoint | Purpose |
|---|---|---|
| **Splunk Web** | **`http://<VM_PUBLIC_IP>:8000`** | SIEM dashboard. User `admin`. |
| Cowrie SSH trap | `ssh -p 2222 root@<VM_PUBLIC_IP>` | The bait. Any password works (see §3). |
| Cowrie Telnet trap | `telnet <VM_PUBLIC_IP> 2223` | Second bait vector. |
| Dionaea FTP | `ftp <VM_PUBLIC_IP> 21` | Malware capture. |
| Dionaea FTP fallback | `ftp <VM_PUBLIC_IP> 2121` | Use when ISPs filter port 21. |
| Real admin SSH | `ssh -i <key>.pem azureuser@<VM_PUBLIC_IP>` | **The real host.** Port 22. |

> **Splunk is HTTP, not HTTPS.** Browsing to `https://<VM_PUBLIC_IP>:8000` fails.
> Use `http://`.

### The dashboard

The Splunk dashboard is titled **"Project Zeus: Executive Security Command"** and
lives at:

```
http://<VM_PUBLIC_IP>:8000/en-US/app/search/mousas_honeynet
```

Its source XML is preserved at
[`config/splunk/dashboards/mousas_honeynet.xml`](config/splunk/dashboards/mousas_honeynet.xml)
and documented panel-by-panel in
[`docs/05-splunk-config.md`](docs/05-splunk-config.md).

---

## Documentation map

Read in order for a full understanding, or jump to what you need.

| # | Document | What it covers |
|---|---|---|
| 01 | [Architecture](docs/01-architecture.md) | Component layout, network topology, data flow |
| 02 | [Azure & host setup](docs/02-azure-and-host-setup.md) | VM sizing, NSG rules, Docker install, directory layout |
| 03 | [Cowrie configuration](docs/03-cowrie-config.md) | SSH honeypot, credential policy, fake filesystem, honeytokens |
| 04 | [Dionaea configuration](docs/04-dionaea-config.md) | Malware-capture honeypot, 16 emulated services, capture paths |
| 05 | [Splunk configuration](docs/05-splunk-config.md) | Ingestion app, `inputs.conf`, `props.conf`, dashboard panels, all SPL |
| 06 | [Log pipeline](docs/06-log-pipeline.md) | The anonymous-volume problem and the `tail -F` bridge that solves it |
| 07 | [Operations runbook](docs/07-operations-runbook.md) | Health checks, is-it-up commands, restart procedures, troubleshooting |
| 08 | [Demo playbook](docs/08-demo-playbook.md) | Red-team vs blue-team live demo script |
| 09 | [Findings & fixes](docs/09-findings-and-fixes.md) | **Six defects found in the live config, with fixes** |
| 10 | [Observed attack data](docs/10-observed-attack-data.md) | Real telemetry: 2,987 events, 318 unique attacker IPs |
| 11 | [Rebuild from scratch](docs/11-rebuild-from-scratch.md) | **Disaster recovery** — empty subscription to working honeynet in ~45 min |

---

## Repository layout

```
.
├── README.md
├── .gitignore
├── config/
│   ├── docker-compose.yml                   # AS-DEPLOYED record (contains the docs/09 defects)
│   ├── docker-compose.fixed.yml             # ← USE THIS to rebuild; F-01/03/04/05 corrected
│   ├── .env.example
│   ├── sync_logs.sh                         # the log bridge
│   ├── systemd/
│   │   └── honeynet-sync.service            # keeps the bridge alive
│   ├── cowrie-custom/
│   │   ├── Dockerfile                       # custom-image build (NOT currently deployed)
│   │   ├── build-fs.py                      # fake-filesystem generator
│   │   ├── userdb.txt                       # credential policy (NOT currently loaded — see F-01)
│   │   └── honeyfs/                         # honeytoken file contents
│   │       ├── etc/{issue.net,motd}
│   │       ├── home/phil/{Project_Zeus_Master_DB_Backup.sql,notes.txt,
│   │       │              deploy.sh,.bash_history}
│   │       └── var/log/auth.log
│   ├── splunk_apps/honeynet_inputs/         # Splunk auto-ingestion app
│   │   ├── default/app.conf
│   │   └── local/{inputs.conf,props.conf}
│   └── splunk/dashboards/
│       └── mousas_honeynet.xml              # the dashboard
└── docs/
    └── 01..10-*.md
```

---

## Quickstart

> **Rebuilding after losing the VM?** Follow
> [docs/11-rebuild-from-scratch.md](docs/11-rebuild-from-scratch.md) instead — it
> is the complete disaster-recovery procedure, uses the *corrected* stack
> definition, and lists exactly what this repo can and cannot restore.
>
> The steps below reproduce the deployment **as it originally ran, defects
> included**, so that the documentation and the historical reality match.

```bash
# 1. Provision an Ubuntu 22.04/24.04 VM, Standard_B2s or larger, 30 GB disk.
#    Open NSG inbound: 22 (your IP only), 2222, 2223, 21, 2121, 8000 (your IP only).

# 2. Install Docker
sudo apt update
sudo apt install -y docker.io docker-compose-plugin jq curl
sudo systemctl enable --now docker
sudo usermod -aG docker $USER   # log out and back in

# 3. Lay down the config
mkdir -p ~/honeynet && cd ~/honeynet
#    copy everything from this repo's config/ directory here
cp .env.example .env && nano .env      # set a real SPLUNK_PASSWORD

# 4. Launch
sudo docker compose up -d

# 5. Wait for Splunk to report healthy (2-5 minutes on a B2s)
watch -n5 'sudo docker ps --format "table {{.Names}}\t{{.Status}}"'

# 6. Install the log bridge (see docs/06-log-pipeline.md for why this is needed)
sudo cp systemd/honeynet-sync.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now honeynet-sync.service

# 7. Import the dashboard
#    Splunk Web > Search & Reporting > Dashboards > Create New > Source
#    Paste config/splunk/dashboards/mousas_honeynet.xml
```

**Before you rely on this deployment, read
[docs/09-findings-and-fixes.md](docs/09-findings-and-fixes.md).** The original
configuration has a container path-mismatch defect that silently disables the
honeytoken file contents and the custom credential policy. `docker-compose.fixed.yml`
corrects it, along with three other findings.

---

## Safety, ethics and legal scope

This honeynet was built for authorized security education. Some ground rules
that are not optional:

- **Only attack infrastructure you own or have written permission to test.**
  The red-team half of the demo playbook assumes the target is your own VM.
- **The captured payloads in `var/lib/cowrie/downloads` are real malware.**
  Roughly 16 MB of it, pulled from the internet by real attackers. Never
  execute those files. Never commit them. The `.gitignore` blocks them.
- **A honeypot is a compromised host by design.** It must stay network-isolated
  from anything you care about. Do not deploy it on a VNet with production
  resources, and do not reuse credentials between the honeypot and anything real.
- **Attacker source IPs are personal data in some jurisdictions.** The observed-
  data document contains real IPs. If you publish this repo under a regime where
  that matters, redact them.
- **Never commit the `.pem` key.** The `.gitignore` blocks `*.pem`, but check
  `git status` before your first push anyway.

---

## Credits

Deployment, configuration and dashboard by the project author. This repository
documents that work; the documentation was assembled by reading the live system.
