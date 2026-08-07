#!/usr/bin/env python3
"""
Generate lab_fs.pickle — Cowrie's fake-filesystem METADATA tree.

Runs INSIDE the cowrie image at docker-build time (see Dockerfile).

Why this exists
---------------
Cowrie's fake filesystem has two independent halves:

  1. METADATA (this pickle) — what `ls` shows: names, sizes, owners, modes.
  2. CONTENTS (honeyfs/)    — what `cat` and `grep` return.

A file must be registered in BOTH or the illusion breaks. A file present only
in honeyfs/ is invisible to `ls`; a file present only in the pickle is visible
but reads back empty. Getting this wrong is the single most common Cowrie
customisation mistake.

Implementation note
-------------------
This script manipulates the pickle directly rather than shelling out to
Cowrie's `fsctl` helper. `fsctl` is not reliably present at a fixed path across
image versions (it may be a console-script in the virtualenv rather than in
bin/), and depending on it made image builds fragile. Direct manipulation has
no such dependency.

Node layout is Cowrie's own (cowrie/shell/fs.py):

    [name, type, uid, gid, size, mode, ctime, contents, target, realfile]
       0     1    2    3     4     5      6        7        8        9

Type constants are NOT hardcoded — they are derived from the base pickle at
runtime by inspecting a known directory (/home) and a known file (/etc/passwd),
so this stays correct even if Cowrie renumbers them.
"""

import os
import pickle
import shutil
import sys
import time

COWRIE_DIR = "/cowrie/cowrie-git"

A_NAME, A_TYPE, A_UID, A_GID, A_SIZE, A_MODE, A_CTIME, A_CONTENTS = range(8)

# Files to register. (path, size_in_bytes, uid, gid, octal_mode)
# Sizes are plausible-looking rather than exact; `ls` shows these, and a
# 0-byte "database backup" reads as obviously fake.
HONEYTOKENS = [
    ("/home/phil/Project_Zeus_Master_DB_Backup.sql", 4096, 1000, 1000, 0o644),
    ("/home/phil/notes.txt",                         1024, 1000, 1000, 0o644),
    ("/home/phil/deploy.sh",                         2048, 1000, 1000, 0o755),
    ("/home/phil/.bash_history",                     2048, 1000, 1000, 0o600),
    ("/var/log/auth.log",                            2048,    0,    0, 0o644),
]


def find_base_pickle():
    for candidate in (
        "share/cowrie/fs.pickle",
        "src/cowrie/data/fs.pickle",
        "data/fs.pickle",
    ):
        path = os.path.join(COWRIE_DIR, candidate)
        if os.path.exists(path):
            return path
    sys.exit(f"ERROR: no base fs.pickle found under {COWRIE_DIR}")


def resolve(node, path):
    """Walk to `path`. Returns the node, or None."""
    if path == "/":
        return node
    cur = node
    for part in path.strip("/").split("/"):
        contents = cur[A_CONTENTS]
        if not isinstance(contents, list):
            return None
        for child in contents:
            if child[A_NAME] == part:
                cur = child
                break
        else:
            return None
    return cur


def detect_types(root):
    """Derive T_DIR / T_FILE from the base pickle instead of hardcoding."""
    home = resolve(root, "/home")
    passwd = resolve(root, "/etc/passwd")
    if home is None or passwd is None:
        sys.exit("ERROR: base pickle lacks /home or /etc/passwd; cannot derive types")
    return home[A_TYPE], passwd[A_TYPE]


def ensure_dir(root, path, t_dir, uid=0, gid=0, mode=0o755):
    """Create `path` as a directory, and every missing parent. Returns the node."""
    cur = root
    for part in path.strip("/").split("/"):
        existing = None
        for child in cur[A_CONTENTS]:
            if child[A_NAME] == part:
                existing = child
                break
        if existing is None:
            existing = [part, t_dir, uid, gid, 4096, mode, int(time.time()), [], None, None]
            cur[A_CONTENTS].append(existing)
            print(f"  + dir  {part}/")
        cur = existing
    return cur


def add_file(root, path, size, uid, gid, mode, t_dir, t_file):
    parent_path, name = path.rsplit("/", 1)
    parent = ensure_dir(root, parent_path, t_dir, uid=uid, gid=gid)

    for child in parent[A_CONTENTS]:
        if child[A_NAME] == name:          # already present — update in place
            child[A_SIZE] = size
            child[A_UID], child[A_GID], child[A_MODE] = uid, gid, mode
            print(f"  ~ file {path}  ({size} bytes)")
            return

    parent[A_CONTENTS].append(
        [name, t_file, uid, gid, size, mode, int(time.time()), None, None, None]
    )
    print(f"  + file {path}  ({size} bytes)")


def main():
    base = find_base_pickle()
    print(f"base pickle: {base}")

    with open(base, "rb") as fh:
        root = pickle.load(fh)

    t_dir, t_file = detect_types(root)
    print(f"derived types: T_DIR={t_dir}  T_FILE={t_file}")

    print("registering honeytokens:")
    for path, size, uid, gid, mode in HONEYTOKENS:
        add_file(root, path, size, uid, gid, mode, t_dir, t_file)

    out_dir = os.path.join(COWRIE_DIR, "share", "cowrie")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "lab_fs.pickle")
    with open(out, "wb") as fh:
        pickle.dump(root, fh)

    # Verify by re-reading — a pickle that will not load is worse than none.
    with open(out, "rb") as fh:
        check = pickle.load(fh)
    for path, _, _, _, _ in HONEYTOKENS:
        if resolve(check, path) is None:
            sys.exit(f"ERROR: verification failed, {path} missing from {out}")

    # Best-effort ownership. AttributeError covers non-POSIX hosts (os.chown is
    # absent on Windows), which matters when self-testing this script outside
    # the container; the Dockerfile does the authoritative chown anyway.
    try:
        os.chown(out, 1000, 1000)
    except (OSError, AttributeError):
        pass

    print(f"OK: wrote and verified {out}")


if __name__ == "__main__":
    main()
