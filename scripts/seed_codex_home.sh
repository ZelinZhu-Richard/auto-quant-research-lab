#!/usr/bin/env bash
# Seed a WRITABLE ~/.codex from a read-only staging mount.
#
# Why: codex hard-fails at startup when its home is read-only (observed
# in minirun_20260712_032156, H005-H007: "failed to initialize in-process
# app-server client: Read-only file system (os error 30)"). Host ~/.codex
# is therefore NEVER mounted rw; the container launcher mounts it
# read-only at ~/.codex-seed and this script copies the credentials into
# a writable in-container home before the orchestrator starts.
#
# No-op when the seed dir is absent, so every launcher calls it
# unconditionally: on the host (shakedown.sh — the real ~/.codex is
# already writable) and in the credential-less dryrun service it does
# nothing.
set -euo pipefail

SEED="${CODEX_SEED_DIR:-$HOME/.codex-seed}"
if [ ! -d "$SEED" ]; then
    exit 0
fi

mkdir -p "$HOME/.codex"
chmod 700 "$HOME/.codex"
seeded=0
for f in auth.json config.toml; do
    if [ -f "$SEED/$f" ]; then
        cp "$SEED/$f" "$HOME/.codex/$f"
        chmod 600 "$HOME/.codex/$f"
        seeded=$((seeded + 1))
    fi
done
if [ "$seeded" -eq 0 ]; then
    echo "seed_codex_home: $SEED exists but holds neither auth.json nor config.toml" >&2
    exit 1
fi
echo "seed_codex_home: seeded $seeded file(s) into writable ~/.codex"
