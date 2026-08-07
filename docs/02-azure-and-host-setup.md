# 02 — Azure & host setup

## 2.1 The VM

| Property | Live value |
|---|---|
| Hostname | `mousahoneypot` |
| OS | Ubuntu (24.04 base), kernel `6.17.0-1011-azure` |
| Architecture | x86_64 |
| Private IP | `10.0.0.4` |
| Subnet | `10.0.0.0/24` |
| MAC | `7C:ED:8D:AC:1C:F3` |
| Public IP | `<VM_PUBLIC_IP>` |
| Admin user | `azureuser` |
| Auth | SSH key (`mousahoneypot_key.pem`) |

### Recommended sizing

```
Size:   Standard_B2s  (2 vCPU, 4 GB RAM)  — minimum
Disk:   30 GB
Public IP: Static (see warning below)
Auth:   SSH key only, never password
```

Splunk is the constraint, not the honeypots. Cowrie and Dionaea are close to
free; Splunk wants 4 GB and will thrash below it. If the Splunk container sits
in `starting` for more than five minutes or flaps between `healthy` and
`unhealthy`, the VM is undersized.

### ⚠️ The public IP will change

This is the single most common way the deployment "breaks."

The IP has already moved once during this project's life: earlier documentation
references `20.233.253.156`, and the current address is `<VM_PUBLIC_IP>`. If the
public IP is allocated **dynamically**, Azure releases it whenever the VM is
deallocated (stopped from the portal) and assigns a new one on next boot.

Consequences when it changes:

- Your `ssh azureuser@<old-ip>` fails.
- The Splunk URL you bookmarked 404s.
- Every NSG rule scoped to "my IP" still works, but every *link* you shared dies.

**Fix it permanently:**

```bash
# Azure CLI — convert the public IP to a static allocation
az network public-ip update \
  --resource-group rg-honeynet-demo \
  --name <public-ip-name> \
  --allocation-method Static
```

**Find the current IP when you have lost it:**

```bash
# From the Azure portal: Virtual machines > mousahoneypot > Overview > Public IP address
# Or via CLI:
az vm list-ip-addresses -g rg-honeynet-demo -n mousahoneypot -o table
```

From *inside* the VM you cannot reliably read the public IP — Azure's instance
metadata service returns an empty `publicIpAddress` for this VM's IP SKU:

```bash
curl -s -H Metadata:true \
  "http://169.254.169.254/metadata/instance/network/interface?api-version=2021-02-01" \
  | python3 -m json.tool
# → "publicIpAddress": ""      ← empty, as observed on this host
```

Use an external echo service instead:

```bash
curl -s ifconfig.me ; echo
```

## 2.2 Resource group

Put everything in one dedicated resource group — `rg-honeynet-demo` or similar.
Teardown then becomes a single operation, and there is no risk of orphaned public
IPs quietly billing you.

```powershell
# Full teardown, PowerShell
Remove-AzResourceGroup -Name rg-honeynet-demo -Force
```

```bash
# Full teardown, Azure CLI
az group delete --name rg-honeynet-demo --yes
```

Do **not** delete `NetworkWatcherRG`. Azure creates it automatically for network
diagnostics; removing it breaks tooling elsewhere in the subscription.

## 2.3 Network Security Group rules

These are the inbound rules the deployment requires. Priorities are suggestions —
what matters is that the `Deny` default stays last.

| Prio | Name | Port | Proto | Source | Action | Why |
|---:|---|---:|---|---|---|---|
| 100 | `admin-ssh` | 22 | TCP | **Your public IP /32** | Allow | Real admin access. Never `Any`. |
| 110 | `splunk-web` | 8000 | TCP | **Your public IP /32** | Allow | SIEM console. Never `Any`. |
| 200 | `cowrie-ssh` | 2222 | TCP | `Any` | Allow | The SSH trap — must be public to catch traffic |
| 210 | `cowrie-telnet` | 2223 | TCP | `Any` | Allow | The Telnet trap |
| 220 | `dionaea-ftp` | 21 | TCP | `Any` | Allow | Primary FTP trap |
| 230 | `dionaea-ftp-alt` | 2121 | TCP | `Any` | Allow | FTP fallback for filtered networks |

### Rules that matter more than they look

**Port 22 must not be open to `Any`.** The honeypot's whole value is that
attacker traffic is *unambiguously* hostile. If the real SSH port is also being
brute-forced, you are cleaning noise out of your own host logs for no benefit —
and one weak moment away from a real compromise. The observed data already shows
attackers probing `azureuser` as a username (see
[doc 10](10-observed-attack-data.md)) — they know it is the Azure default.

**Port 8000 must not be open to `Any`.** Splunk Web on plain HTTP with a
password in an environment variable is not an internet-facing control plane. As
currently deployed it appears to be reachable publicly; see
[finding F-05](09-findings-and-fixes.md).

**ICMP is dropped by Azure regardless of NSG rules.** This breaks naive `ping`
and breaks Nmap's default host-discovery phase. Any scan against this host needs
`-Pn`:

```bash
nmap -Pn -p 21,2121,2222,2223,8000 <VM_PUBLIC_IP>
```

Without `-Pn`, Nmap concludes the host is down and never scans a single port.
This wasted real time during the original build; do not rediscover it.

## 2.4 Host firewall (UFW)

**UFW is disabled on this host.**

```bash
$ sudo ufw status verbose
Status: inactive
```

This is deliberate and, in this specific case, correct. Docker's `docker-proxy`
inserts its own `iptables` DNAT rules that bypass UFW's `INPUT` chain entirely —
running UFW alongside published Docker ports produces a firewall that *appears*
to be filtering while actually allowing everything. Rather than maintain that
illusion, the deployment lets the Azure NSG be the single enforcement point.

The trade-off: **the NSG is now the only thing between the internet and this
host.** If someone loosens an NSG rule, there is no second layer. Audit the NSG
rather than trusting UFW.

## 2.5 Docker installation

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin jq curl nano lftp netcat-openbsd
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker $USER
```

Log out and back in for the group change to take effect. In practice the
deployment is driven with `sudo docker …` throughout, which sidesteps the issue.

### The Compose V1 trap

The original build hit a hard failure with the legacy `docker-compose` v1.29
Python script:

```
KeyError: 'ContainerConfig'
```

This is a known incompatibility between Compose v1 and modern Docker Engine
image metadata. The fix is to stop using v1 entirely and use the Go-based
**Compose V2 plugin**, invoked as `docker compose` (space, not hyphen):

```bash
# WRONG — legacy v1, will fail
docker-compose up -d

# RIGHT — Compose V2 plugin
sudo docker compose up -d
```

If `docker compose version` reports "is not a docker command", install the
plugin system-wide:

```bash
sudo apt install -y docker-compose-plugin
# or, manually:
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
```

If orphaned v1-era containers block V2 from starting, clear them:

```bash
sudo docker rm -f $(sudo docker ps -aq)
```

## 2.6 Directory layout on the VM

Deployment root is `/home/azureuser/honeynet`:

```
/home/azureuser/honeynet/            (mode 0777 — see F-06)
├── docker-compose.yml               the live stack definition
├── .env                             SPLUNK_PASSWORD (currently unused — see F-04)
├── sync_logs.sh                     the log bridge
├── cowrie-custom/
│   ├── Dockerfile                   custom-image recipe (built, then abandoned)
│   ├── build-fs.py                  fake-filesystem generator
│   ├── userdb.txt                   credential policy: `root:x:*`
│   ├── lab_fs.pickle                1.2 MB fake-filesystem metadata (binary)
│   └── honeyfs/
│       ├── etc/{issue.net,motd}
│       ├── home/phil/{Project_Zeus_Master_DB_Backup.sql,notes.txt,
│       │              deploy.sh,.bash_history}
│       └── var/log/auth.log
├── cowrie-logs/
│   └── cowrie.json                  bridged copy, ~2.6 MB
├── dionaea-logs/
│   ├── dionaea.log                  ~2.1 MB
│   └── dionaea-errors.log           ~126 KB
├── splunk_apps/honeynet_inputs/
│   ├── default/app.conf
│   └── local/{inputs.conf,props.conf}
└── extracted_userdb.txt             (empty — debugging artifact, safe to delete)
```

### ⚠️ Directory permissions

`~/honeynet` is mode **0777**, world-writable, applied during troubleshooting via
`sudo chmod -R 777 ~/honeynet`. It resolved a container-writes-to-bind-mount
permission error at the cost of letting any local user modify the honeypot's
configuration and rewrite its logs. Since this host has exactly one human user,
the practical risk is low — but a honeypot is a system you *expect* to be
attacked, and world-writable log files are exactly what an attacker who achieves
container escape would target to erase their tracks.

See [finding F-06](09-findings-and-fixes.md) for the corrected permission model.

## 2.7 Systemd units

One custom unit, `honeynet-sync.service`, keeps the log bridge alive. Its full
behaviour is documented in [doc 06](06-log-pipeline.md). Installed at
`/etc/systemd/system/honeynet-sync.service`, `enabled`, currently `active
(running)`.

## 2.8 Cron

One user-level cron entry exists as a belt-and-braces duplicate of the systemd
unit:

```cron
@reboot sleep 30 && /home/azureuser/honeynet/sync_logs.sh
```

Root has no crontab.

**This entry is redundant and should be removed.** `honeynet-sync.service` is
already `enabled` and `Restart=always`, so it starts the bridge at boot on its
own. Having both means two `tail -F` processes racing to append to the same file
after every reboot — which is precisely the duplicate-event bug ("triplets" in
the original build notes) that the systemd unit was written to fix. See
[finding F-02](09-findings-and-fixes.md).

```bash
# Inspect
crontab -l
# Remove the @reboot line
crontab -e
```

---

Next: [03 — Cowrie configuration](03-cowrie-config.md)
