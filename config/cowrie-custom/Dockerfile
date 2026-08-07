# Custom Cowrie image for the Project Zeus honeynet.
#
# Bakes the honeytoken contents, the credential policy, and the fake-filesystem
# metadata into the image, so no bind mounts are needed at runtime.
#
# IMPORTANT: every path below uses /cowrie/... — NOT /home/cowrie/...
# The cowrie/cowrie image installs to /cowrie (COWRIE_HOME=/cowrie) and its
# WorkingDir is /cowrie/cowrie-git. Mounting to /home/cowrie/... silently does
# nothing; that mistake is documented as finding F-01.

FROM cowrie/cowrie:latest

USER root

# 1. File CONTENTS — what `cat` and `grep` return.
COPY --chown=cowrie:cowrie honeyfs/ /cowrie/cowrie-git/honeyfs/

# 2. Credential policy — which logins succeed.
COPY --chown=cowrie:cowrie userdb.txt /cowrie/cowrie-git/etc/userdb.txt

# 3. File METADATA — what `ls` shows. GENERATED at build time from the base
#    fs.pickle shipped in the image, so no binary artifact is committed to git.
#    (Pickles execute arbitrary code on load; generating beats vendoring.)
COPY build-fs.py /tmp/build-fs.py
RUN /cowrie/cowrie-env/bin/python3 /tmp/build-fs.py \
    && rm -f /tmp/build-fs.py \
    && chown cowrie:cowrie /cowrie/cowrie-git/share/cowrie/lab_fs.pickle

# Point Cowrie's shell at the generated filesystem.
ENV COWRIE_SHELL_FILESYSTEM=/cowrie/cowrie-git/share/cowrie/lab_fs.pickle

USER cowrie
