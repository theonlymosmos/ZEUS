# 03 — Cowrie configuration (SSH/Telnet honeypot)

Cowrie is the primary sensor and the source of essentially all the interesting
telemetry. This document covers what is deployed, how each piece is wired, and —
importantly — **which pieces are wired to paths Cowrie never reads.**

## 3.1 Runtime facts

Captured from the live container:

```
Cowrie Version   2.9.17.dev1+gcd0770d3d
Python Version   3.13.5
Twisted Version  25.5.0
Sensor UUID      bafc61de-32ec-11f1-a37e-46de337b5ff9
Image            cowrie/cowrie:latest
Container        cowrie_honeypot
Container host   prod-app-01        (Docker `hostname:` — NOT what the attacker sees)
Shell prompt     svr04              (Cowrie `[honeypot] hostname`, .dist default)
Listeners        SSH  0.0.0.0:2222
                 Telnet 0.0.0.0:2223
Output engine    jsonlog
```

Startup log, first line — remember this one, it explains a lot:

```
2026-04-28T14:49:23+0000 [-] Reading configuration from ['/cowrie/cowrie-git/etc/cowrie.cfg.dist']
```

**There is no `cowrie.cfg`.** Cowrie fell back to the shipped `.dist` defaults.
Every setting is stock unless overridden by an environment variable.

> ### ⚠️ The attacker sees `svr04`, not `prod-app-01`
>
> The Compose file sets `hostname: prod-app-01`, but that is the **Docker
> container's** hostname — it only affects the container's own `/etc/hostname`.
> Cowrie's emulated shell takes its prompt from its own config key,
> `[honeypot] hostname`, whose `.dist` default is `svr04`. With no `cowrie.cfg`
> to override it, that default wins.
>
> Confirmed from a real captured session replay:
>
> ```
> root@svr04:~# cd /home
> root@svr04:/home# ls
> phil
> ```
>
> `svr04` is Cowrie's stock hostname and a known honeypot fingerprint — an
> attacker who recognises it knows immediately what they are on. To fix, set the
> environment variable in the Compose file:
>
> ```yaml
>     environment:
>       - COWRIE_HONEYPOT_HOSTNAME=prod-app-01
> ```
>
> The session replay also shows the shell serving `/home/phil` correctly, which
> independently confirms `lab_fs.pickle` is loading (see §3.3).

## 3.2 The Compose service

From `config/docker-compose.yml`:

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
      - COWRIE_SHELL_FILESYSTEM=/home/cowrie/cowrie-git/share/cowrie/lab_fs.pickle
    volumes:
      - ./cowrie-logs:/home/cowrie/cowrie-git/var/log/cowrie
      - ./cowrie-custom/honeyfs:/home/cowrie/cowrie-git/honeyfs:ro
      - ./cowrie-custom/userdb.txt:/home/cowrie/cowrie-git/etc/userdb.txt:ro
      - ./cowrie-custom/lab_fs.pickle:/home/cowrie/cowrie-git/share/cowrie/lab_fs.pickle:ro
    restart: always
    networks:
      - honeynet
```

### The environment-variable override mechanism

Cowrie maps environment variables onto config keys by the rule:

```
COWRIE_<SECTION>_<KEY>   →   [section] key
```

So the two variables above mean:

| Variable | Config equivalent |
|---|---|
| `COWRIE_TELNET_ENABLED=yes` | `[telnet]` → `enabled = yes` |
| `COWRIE_SHELL_FILESYSTEM=…lab_fs.pickle` | `[shell]` → `filesystem = …lab_fs.pickle` |

This is why the deployment works at all without a `cowrie.cfg`: the two settings
that had to change were changed through the environment.

## 3.2b How Cowrie is installed inside the container

**This section is the root cause of everything in §3.3 and of the entire log
pipeline in [doc 06](06-log-pipeline.md).** Read it before either.

### The install layout

Cowrie is not installed the way a normal Debian package would be. From the
image metadata:

```console
$ sudo docker inspect cowrie/cowrie:latest --format '...'
WorkingDir: /cowrie/cowrie-git
User:       cowrie
Entrypoint: ["/cowrie/cowrie-env/bin/python3"]
Cmd:        ["/cowrie/cowrie-env/bin/twistd","-n","--umask=0022","--pidfile=","cowrie"]
Volumes:    {"/cowrie/cowrie-git/etc":{},"/cowrie/cowrie-git/var":{}}
```

```
/cowrie/                          ← COWRIE_HOME
├── cowrie-env/                    Python virtualenv (the interpreter)
│   └── bin/{python3,twistd}
└── cowrie-git/                   ← WorkingDir. Everything relative resolves here.
    ├── bin/                       createdynamicprocess, regen-dropin.cache ONLY
    ├── src/cowrie/                the application code (PYTHONPATH)
    │   ├── data/                  fs.pickle lives here
    │   └── scripts/               playlog, fsctl, createfs, asciinema
    │                              — run as `python3 -m cowrie.scripts.<name>`,
    │                              NOT as bin/ executables (verified 2026-08)
    ├── etc/                      ★ DECLARED VOLUME — cowrie.cfg, userdb.txt
    ├── honeyfs/                   fake file CONTENTS
    ├── share/cowrie/              shared data
    ├── txtcmds/                   canned command output
    └── var/                      ★ DECLARED VOLUME — all runtime output
        ├── log/cowrie/            cowrie.json, cowrie.log
        └── lib/cowrie/            tty/, downloads/, snapshots/
```

Two things here cause every problem in this deployment:

1. **The install root is `/cowrie`, not `/home/cowrie`.** There is no `/home/cowrie`
   in the image at all.
2. **The image declares two anonymous volumes**: `/cowrie/cowrie-git/etc` and
   `/cowrie/cowrie-git/var`.

### The default paths — all relative

Every path in `cowrie.cfg.dist` is **relative**, and resolves against the
WorkingDir `/cowrie/cowrie-git`:

| Setting | Line | Default value | Resolves to |
|---|---:|---|---|
| `log_path` | 35 | `var/log/cowrie` | `/cowrie/cowrie-git/var/log/cowrie` ★ |
| `state_path` | 53 | `var/lib/cowrie` | `/cowrie/cowrie-git/var/lib/cowrie` ★ |
| `download_path` | 41 | `${state_path}/downloads` | `…/var/lib/cowrie/downloads` ★ |
| `ttylog_path` | 100 | `${state_path}/tty` | `…/var/lib/cowrie/tty` ★ |
| `etc_path` | 59 | `etc` | `/cowrie/cowrie-git/etc` ★ |
| `contents_path` | 69 | `honeyfs` | `/cowrie/cowrie-git/honeyfs` |
| `data_path` | 47 | `src/cowrie/data` | `/cowrie/cowrie-git/src/cowrie/data` |
| `txtcmds_path` | 83 | `txtcmds` | `/cowrie/cowrie-git/txtcmds` |
| `filesystem` (`[shell]`) | 441 | `${data_path}/fs.pickle` | `…/src/cowrie/data/fs.pickle` |

★ = falls **inside a declared VOLUME**.

### 🔑 This is the bug you fixed

Here is the chain, start to finish:

1. `log_path = var/log/cowrie` is relative → resolves to
   `/cowrie/cowrie-git/var/log/cowrie`.
2. That path is **inside the image's declared `VOLUME /cowrie/cowrie-git/var`**.
3. The Compose file gives Docker a bind mount for
   `/home/cowrie/cowrie-git/var/log/cowrie` — a completely different path.
4. So Docker has a declared volume with **no host binding supplied**. Its rule in
   that situation is to create an **anonymous volume** with a random 64-hex name.
5. Cowrie writes its logs, TTY replays and captured payloads into that anonymous
   volume. Nothing errors. Nothing warns.
6. `~/honeynet/cowrie-logs/` — the directory Splunk watches — stays empty.

The same mechanism hits `etc` independently: `VOLUME /cowrie/cowrie-git/etc`
becomes its own anonymous volume containing only the image's stock files, which
is why `userdb.txt` never loads:

```console
$ sudo ls /var/lib/docker/volumes/30328f27…/_data
.gitignore  cowrie.cfg.dist  userdb.example      ← no userdb.txt, no cowrie.cfg
```

`contents_path` (honeyfs) is *not* inside a declared volume, so it does not get
an anonymous volume — it just quietly keeps using the stock directory baked into
the image, because the custom one was mounted at `/home/cowrie/…`.

### How it was solved

Not by fixing the paths — by building a bridge around them. `sync_logs.sh` asks
Docker at runtime where the anonymous volume for `/cowrie/cowrie-git/var` landed,
then `tail -F`s the log out of it into the directory Splunk watches:

```bash
NEW_PATH=$(sudo docker inspect cowrie_honeypot \
  --format='{{range .Mounts}}{{if eq .Destination "/cowrie/cowrie-git/var"}}{{.Source}}{{end}}{{end}}')
exec sudo tail -F $NEW_PATH/log/cowrie/cowrie.json >> /home/azureuser/honeynet/cowrie-logs/cowrie.json
```

`honeynet-sync.service` keeps that alive across reboots and container recreation.
The key insight in that one-liner: it matches on the **destination** path
(`/cowrie/cowrie-git/var`, which is stable) to discover the **source** path (the
random volume name, which changes on every recreate).

Full detail — including why `-F` rather than `-f`, and how to debug it — is in
[doc 06](06-log-pipeline.md). The permanent fix that removes the need for the
bridge entirely is [F-01](09-findings-and-fixes.md).

---

## 3.3 ⚠️ The `/home/cowrie` vs `/cowrie` path mismatch

**This is the most consequential defect in the deployment. Read this section
before trusting anything about the honeytokens.**

Every volume mount in the Compose file targets a path under
`/home/cowrie/cowrie-git/…`. The `cowrie/cowrie` image does not install Cowrie
there. It installs to `/cowrie/cowrie-git/…`:

```
COWRIE_HOME=/cowrie
PYTHONPATH=/cowrie/cowrie-git/src
```

So Docker dutifully creates `/home/cowrie/cowrie-git/…` inside the container,
mounts the files there, and Cowrie — reading from `/cowrie/cowrie-git/…` —
never looks at any of it.

### Verified evidence

Listing the honeyfs at the path **Cowrie actually reads**:

```console
$ sudo docker cp cowrie_honeypot:/cowrie/cowrie-git/honeyfs - | tar -tv
drwxr-xr-x 999/999    0  honeyfs/
drwxr-xr-x 999/999    0  honeyfs/etc/
-rw-r--r-- 999/999  538  honeyfs/etc/group
-rw-r--r-- 999/999    9  honeyfs/etc/host.conf
-rw-r--r-- 999/999    6  honeyfs/etc/hostname
-rw-r--r-- 999/999  184  honeyfs/etc/hosts
-rw-r--r-- 999/999 2013  honeyfs/etc/inittab
-rw-r--r-- 999/999   26  honeyfs/etc/issue
-rw-r--r-- 999/999    0  honeyfs/etc/issue.net        ← 0 bytes: STOCK, not ours
-rw-r--r-- 999/999  286  honeyfs/etc/motd             ← 286 bytes: STOCK, not ours
-rw-r--r-- 999/999  868  honeyfs/etc/passwd
-rw-r--r-- 999/999  750  honeyfs/etc/shadow
drwxr-xr-x 999/999    0  honeyfs/proc/
...
                                                       ← NO honeyfs/home/ AT ALL
```

Listing the honeyfs at the path **the bind mount targets**:

```console
$ sudo docker cp cowrie_honeypot:/home/cowrie/cowrie-git/honeyfs - | tar -tv
drwxrwxrwx 1000/114   0  honeyfs/
drwxrwxrwx 1000/114   0  honeyfs/etc/
-rwxrwxrwx 1000/114  88  honeyfs/etc/issue.net                          ← ours
-rwxrwxrwx 1000/114 277  honeyfs/etc/motd                               ← ours
drwxrwxrwx 1000/114   0  honeyfs/home/
drwxrwxrwx 1000/114   0  honeyfs/home/phil/
-rwxrwxrwx 1000/114 189  honeyfs/home/phil/.bash_history                ← ours
-rwxrwxrwx 1000/114  93  honeyfs/home/phil/Project_Zeus_Master_DB_Backup.sql
-rwxrwxrwx 1000/114 240  honeyfs/home/phil/deploy.sh
-rwxrwxrwx 1000/114 184  honeyfs/home/phil/notes.txt
drwxrwxrwx 1000/114   0  honeyfs/var/
-rwxrwxrwx 1000/114 446  honeyfs/var/log/auth.log
```

All the custom content is sitting in an **orphan directory that Cowrie never
opens**.

### Why the illusion still half-works

Cowrie's fake filesystem has two independent halves:

| Half | What it provides | Where it comes from | Status here |
|---|---|---|---|
| **Metadata (pickle)** | What `ls` shows — names, sizes, owners, permissions | `[shell] filesystem` | ✅ **Working** |
| **Contents (honeyfs)** | What `cat` / `grep` return | `[honeypot] contents_path` | ❌ **Broken** |

The pickle path *happens* to be correct, because `COWRIE_SHELL_FILESYSTEM` points
at `/home/cowrie/cowrie-git/share/cowrie/lab_fs.pickle` — which is exactly where
the bind mount put it. Verified present in the container:

```console
$ sudo docker cp cowrie_honeypot:/home/cowrie/cowrie-git/share/cowrie/lab_fs.pickle - | tar -tv
-rwxrwxrwx 1000/114 1261954  lab_fs.pickle
```

And the pickle genuinely contains the honeytoken metadata:

```console
$ python3 -c "import pickle; ..."   # walk the fs tree, filter /home
lab_fs.pickle entries under /home:
   /home                                          size=4096
   /home/phil                                     size=4096
   /home/phil/.bash_history                       size=2048
   /home/phil/.bash_logout                        size=220
   /home/phil/.bashrc                             size=3392
   /home/phil/.profile                            size=675
   /home/phil/Project_Zeus_Master_DB_Backup.sql   size=4096
   /home/phil/deploy.sh                           size=2048
   /home/phil/notes.txt                           size=1024
```

Meanwhile `contents_path` was never overridden, so it uses the `.dist` default —
a path relative to Cowrie's install root, resolving to `/cowrie/cowrie-git/honeyfs`,
i.e. the stock honeyfs with no `/home/phil` in it.

### The attacker's actual experience

```console
root@prod-app-01:~# cd /home/phil
root@prod-app-01:/home/phil# ls -la
total 20
drwxr-xr-x 1 phil phil 4096 ... .
-rw-r--r-- 1 phil phil 4096 ... Project_Zeus_Master_DB_Backup.sql   ← visible!
-rw-r--r-- 1 phil phil 1024 ... notes.txt                           ← visible!
-rw-r--r-- 1 phil phil 2048 ... deploy.sh                           ← visible!
-rw------- 1 phil phil 2048 ... .bash_history                       ← visible!

root@prod-app-01:/home/phil# cat Project_Zeus_Master_DB_Backup.sql
root@prod-app-01:/home/phil#                                        ← EMPTY.
```

The bait is visible but unreadable. Real attackers did exactly this — the command
log shows `cat Project_Zeus_Master_DB_Backup.sql` executed **6 times** — and got
nothing back.

**Impact:** the deception is weaker than intended, and the headline demo moment
(`grep -i password Project_Zeus_Master_DB_Backup.sql` printing fake credentials
on screen) does not work. Detection is unaffected — every command is still logged
perfectly. The fix is in [finding F-01](09-findings-and-fixes.md).

## 3.4 Credential policy

### What is configured

`config/cowrie-custom/userdb.txt` — a single line:

```
root:x:*
```

Meaning: user `root`, any password. Deliberately trivial, so attackers get in
fast and start generating behavioural telemetry.

### What is actually loaded

Nothing. From the startup log:

```
2026-04-28T14:53:19+0000 [HoneyPotSSHTransport,0,138.2.102.x] Could not read etc/userdb.txt, default database activated
```

Same path mismatch: the file is mounted at `/home/cowrie/cowrie-git/etc/userdb.txt`,
Cowrie looks in `/cowrie/cowrie-git/etc/userdb.txt`. And that directory is an
anonymous Docker volume containing only the image's stock files:

```console
$ sudo ls /var/lib/docker/volumes/30328f27…/_data
.gitignore
cowrie.cfg.dist
userdb.example        ← no userdb.txt, no cowrie.cfg
```

So Cowrie falls back to its **built-in default credential database**, which
matches `userdb.example`:

```
root:x:!root            # DENY  root/root
root:x:!123456          # DENY  root/123456
root:x:!/honeypot/i     # DENY  any password matching /honeypot/i
root:x:*                # ALLOW root/anything-else
tomcat:x:*              # ALLOW tomcat/anything
oracle:x:*              # ALLOW oracle/anything
*:x:somepassword        # ALLOW any user with password "somepassword"
*:x:*                   # ALLOW everything else
```

Rules are evaluated top-down and **processing stops at the first match**.

### ⚠️ The demo password does not work

Older project notes instruct the red team to log in as `root` / `123456`. Under
the default database, **line 2 explicitly denies that exact combination.** That
login will fail on stage.

This is confirmed by the real data — no successful login ever used `123456`,
while these did succeed:

| Password used | Successes |
|---|---:|
| `admin` | 13 |
| `P` | 10 |
| *(empty string)* | 7 |
| `password` | 1 |
| `12321321` | 1 |

**Use `root` / `admin` for demos**, or fix the mount so `userdb.txt` actually
loads (F-01).

### `userdb.txt` syntax reference

```
username:unused:password
```

- Fields are colon-separated; the middle field is ignored.
- `*` matches any username or any password.
- `!` prefix on the password **denies** that password.
- `/regex/i` allows a case-insensitive regular expression.
- First matching line wins.

A more realistic policy than `root:x:*`, which lets you also harvest *failed*
guesses:

```
root:x:!root
root:x:!123456
root:x:123456789
root:x:admin
root:x:Winter2026!
phil:x:Winter2026!
admin:x:admin123
*:x:!*
```

## 3.5 The fake filesystem (`lab_fs.pickle`)

### What it is

A Python pickle holding the *metadata tree* of the emulated filesystem: every
path, its type, uid, gid, size, mode and mtime. Cowrie loads it at startup and
serves `ls`, `stat`, `cd` and tab-completion entirely from it.

It does **not** contain file contents. Contents come from `honeyfs/`. This split
is the single most confusing thing about Cowrie customisation and the direct
cause of finding F-01.

### How it is generated

The pickle is **not committed to this repository.** Python pickles execute
arbitrary code when loaded, so a 1.2 MB pickle sitting in a public repo is a
supply-chain hazard for anyone who clones it. It is generated instead, at
`docker build` time, by `config/cowrie-custom/build-fs.py`:

1. Locate the stock `fs.pickle` shipped inside the image — tries
   `share/cowrie/fs.pickle`, `src/cowrie/data/fs.pickle`, `data/fs.pickle`.
2. **Derive Cowrie's type constants** by inspecting known nodes (`/home` for
   `T_DIR`, `/etc/passwd` for `T_FILE`) rather than hardcoding them, so the
   script stays correct if upstream renumbers them.
3. Create `/home/phil` and register each honeytoken with a plausible size,
   owner and mode.
4. Write `share/cowrie/lab_fs.pickle`, then **re-read it and verify** every
   honeytoken resolves — exiting non-zero if not, so a broken pickle fails the
   image build rather than shipping silently.

The size argument is the important detail: it makes `ls -la` show a 4096-byte
SQL dump rather than an obviously-fake zero-byte file.

> **Why not `fsctl`?** Cowrie ships an `fsctl` helper for editing filesystem
> pickles, and the original script shelled out to it. Its location is not stable
> across image versions — it may be a console-script inside the virtualenv
> rather than in `bin/` — which made builds fragile. Manipulating the pickle
> directly removes the dependency entirely.

The node layout, for reference:

```
[name, type, uid, gid, size, mode, ctime, contents, target, realfile]
   0     1    2    3     4     5      6        7        8        9
```

`contents` is a list of child nodes for directories, `None` for files. That is
the whole structure — the "filesystem" is a nested list.

### Regenerating

```bash
cd ~/honeynet
sudo docker compose build --no-cache cowrie
```

Expected output:

```
base pickle: /cowrie/cowrie-git/src/cowrie/data/fs.pickle
derived types: T_DIR=... T_FILE=...
registering honeytokens:
  + dir  phil/
  + file /home/phil/Project_Zeus_Master_DB_Backup.sql  (4096 bytes)
  ...
OK: wrote and verified /cowrie/cowrie-git/share/cowrie/lab_fs.pickle
```

The script is idempotent — rebuilding updates entries in place rather than
duplicating them.

Independent verification, if you want to inspect the result yourself:

```bash
sudo docker run --rm --entrypoint /cowrie/cowrie-env/bin/python3 \
  honeynet-cowrie-custom:latest -c "
import pickle
d = pickle.load(open('/cowrie/cowrie-git/share/cowrie/lab_fs.pickle','rb'))
def walk(n, path=''):
    full = path + '/' + n[0] if n[0] not in ('/', '') else '/'
    if full.startswith('/home'):
        print(f'  {full:<50} size={n[4]}')
    if isinstance(n[7], list):
        for c in n[7]:
            walk(c, '' if full == '/' else full)
walk(d)
"
```

## 3.6 Honeytokens

A **honeytoken** is a piece of fake data whose only purpose is to be stolen.
Nobody with legitimate access has any reason to read it, so any access at all is
a high-confidence intrusion signal.

Five are planted. All live in `config/cowrie-custom/honeyfs/`.

### `home/phil/Project_Zeus_Master_DB_Backup.sql` — the primary bait

The intended content:

```sql
-- Master DB Backup - Project Zeus
-- Generated by backup-db.sh at 02:00 UTC
-- WARNING: rotate these credentials after migration.

CREATE DATABASE zeus_prod;
USE zeus_prod;

INSERT INTO users (username, password, role) VALUES
('admin', 'Zeus_Admin_Access_99!', 'superadmin'),
('backup_svc', 'BackupSvc_2026_DoNotShare!', 'service'),
('reporting', 'ReadOnly_Report_7781!', 'readonly');
```

**The deployed file is corrupted.** It reads, in its entirety:

```sql
INSERT INTO users (username, password) VALUES ('admin', 'Zeus_Admin_Access_99' 2>/dev/null);
```

A shell redirect (`2>/dev/null`) leaked into the file during a heredoc write, and
the trailing `!` was lost to bash history expansion. Restore it with a
**quoted** heredoc — the quotes around `'EOF'` are what disable expansion:

```bash
cat > cowrie-custom/honeyfs/home/phil/Project_Zeus_Master_DB_Backup.sql <<'EOF'
-- Master DB Backup - Project Zeus
...
EOF
```

### `home/phil/notes.txt` — the plausibility prop

```
TODO:
- rotate Zeus database password after migration
- remove old SQL dump from home directory
- verify Splunk forwarder status
- disable test admin account before production handoff
```

Every line reinforces the story: a real engineer, mid-migration, who knows the
dump should not be there. It also nudges the attacker toward the SQL file.

### `home/phil/deploy.sh` — credentials in an env block

```bash
#!/bin/bash
export DB_HOST=10.10.8.15
export DB_NAME=zeus_prod
export DB_USER=backup_svc
export DB_PASS=BackupSvc_2026_DoNotShare!

echo "Deploying Project Zeus API..."
# rsync -avz ./build/ zeus-api:/srv/zeus/
# systemctl restart zeus-api
```

Baits lateral movement: an attacker who reads this now believes there is a
database server at `10.10.8.15`.

### `home/phil/.bash_history` — the breadcrumb trail

```bash
ssh admin@10.10.8.15
mysql -u backup_svc -p zeus_prod
cat Project_Zeus_Master_DB_Backup.sql | grep password
scp Project_Zeus_Master_DB_Backup.sql backup@10.10.8.20:/mnt/backups/
history -c
```

The best of the five. Checking `.bash_history` is standard attacker tradecraft,
and this one hands them a map: two more hosts, a database name, a service
account, and — the nice touch — a `history -c` at the end implying the previous
occupant had something to hide.

### `var/log/auth.log` — the corroborating record

```
Apr 27 01:58:31 prod-app-01 sshd[2188]: Accepted password for phil from 10.10.4.22 port 51244 ssh2
Apr 27 02:00:11 prod-app-01 sudo: phil : TTY=pts/0 ; PWD=/home/phil ; USER=root ; COMMAND=/usr/local/bin/backup-db.sh
Apr 27 02:03:44 prod-app-01 sshd[2331]: Failed password for invalid user test from 185.220.101.45 port 39412 ssh2
Apr 27 02:04:02 prod-app-01 sshd[2331]: Connection closed by invalid user test 185.220.101.45 port 39412 [preauth]
```

Corroborates `phil` as a real user and shows the host getting scanned by others —
consistent with an internet-facing box. (`185.220.101.45` is a well-known Tor
exit node, a realistic touch.)

### `etc/issue.net` and `etc/motd` — the front door

```
Ubuntu 22.04.4 LTS prod-app-01 ttyS0
Authorized access only. Activity may be monitored.
```

```
Welcome to Ubuntu 22.04.4 LTS (GNU/Linux 5.15.0-azure x86_64)

System information as of login:
  System load:  0.17
  Usage of /:   41.2% of 29.0GB
  Memory usage: 38%
  Swap usage:   0%
  Processes:    124

Last successful backup: /home/phil/Project_Zeus_Master_DB_Backup.sql
```

The MOTD's last line is the hook — it names the bait file at the moment of login,
before the attacker has run a single command.

> ⚠️ Neither banner is currently served, for the same reason as everything else in
> `honeyfs/` (F-01). Attackers see Cowrie's stock 0-byte `issue.net` and stock
> 286-byte `motd`.

## 3.7 The custom image (built, then abandoned)

`config/cowrie-custom/Dockerfile` exists and was built during development:

```dockerfile
FROM cowrie/cowrie:latest

COPY --chown=cowrie:cowrie honeyfs/ /cowrie/cowrie-git/honeyfs/
COPY --chown=cowrie:cowrie userdb.txt /cowrie/cowrie-git/etc/userdb.txt
COPY --chown=cowrie:cowrie lab_fs.pickle /cowrie/cowrie-git/src/cowrie/data/lab_fs.pickle

ENV COWRIE_SHELL_FILESYSTEM=/cowrie/cowrie-git/src/cowrie/data/lab_fs.pickle
```

**Note that this Dockerfile uses the correct `/cowrie/…` paths.** It would have
worked. The live `docker-compose.yml` does not use it — it pulls
`cowrie/cowrie:latest` directly and bind-mounts over it with the wrong paths.

Baking content into an image is strictly better than bind-mounting it for a
honeypot: the layout is fixed at build time, an attacker cannot reach the host
filesystem through a mount, and there is no read-only/ownership friction. The
recommended fix in F-01 is to return to this image.

## 3.8 Where Cowrie writes its data

Because the log bind mount also targets `/home/cowrie/…`, Cowrie's `var/`
directory became an **anonymous Docker volume**:

```
/var/lib/docker/volumes/6e416b9fdb9b72e86a645442d148f992ab0dfd00004115c96b72c455305550a3/_data/
├── log/cowrie/
│   ├── cowrie.json                  1,005,184 bytes  (live)
│   ├── cowrie.json.2026-04-28         406,511 bytes
│   ├── cowrie.json.2026-05-03          53,235 bytes
│   └── cowrie.json.2026-05-04         148,001 bytes
├── lib/cowrie/
│   ├── tty/                         19 session recordings
│   ├── downloads/                   31 captured payloads, 16 MB
│   └── snapshots/
└── run/
```

Finding this volume, and keeping Splunk fed from it, is what
[doc 06](06-log-pipeline.md) is about.

### TTY session replay

The forensic showpiece. Every interactive session is recorded byte-for-byte and
replays with original timing:

```bash
# List recordings (filenames are SHA-256 of the stream)
sudo ls -la /var/lib/docker/volumes/6e416b9f*/\_data/lib/cowrie/tty/

# Replay one
sudo docker exec -it cowrie_honeypot \
  /cowrie/cowrie-env/bin/python3 -m cowrie.scripts.playlog /cowrie/cowrie-git/var/lib/cowrie/tty/<FILENAME>
```

> Note the `/cowrie/…` prefix, not `/home/cowrie/…`. Older project notes use the
> wrong path here too and will produce "file not found".

### Captured payloads

31 files, ~16 MB, named by SHA-256 — which means the filename *is* the
VirusTotal search term:

```bash
V=/var/lib/docker/volumes/6e416b9fdb9b72e86a645442d148f992ab0dfd00004115c96b72c455305550a3/_data
sudo ls -1 $V/lib/cowrie/downloads
sudo sh -c "find $V/lib/cowrie/downloads -type f -exec sha256sum {} \;"
```

Files named `<timestamp>-<session>-redir__<path>` are *shell redirect* captures —
content the attacker wrote with `cat > file <<EOF` rather than downloaded.

> 🚨 **These are real, live malware samples collected from the internet.** Do not
> execute them, do not copy them to a Windows machine without an EICAR-aware AV,
> and do not commit them to git.

---

Next: [04 — Dionaea configuration](04-dionaea-config.md)
