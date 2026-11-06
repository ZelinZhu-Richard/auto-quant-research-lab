#!/usr/bin/env bash
# Seed a WRITABLE ~/.codex from a read-only staging mount — auth.json ONLY.
#
# Why a seed at all: codex hard-fails at startup when its home is
# read-only (minirun_20260712_032156: "failed to initialize in-process
# app-server client: Read-only file system (os error 30)"). Host
# ~/.codex is therefore NEVER mounted rw; the launcher mounts the single
# credential file read-only at ~/.codex-seed/auth.json.
#
# Why auth.json only: model identity and effort are pinned by orchestrator
# flags (orchestrator/stages.py), so the container needs no personal
# config.toml — importing it would activate host plugin/notify/MCP
# configuration inside the unattended container (R6 isolation).
#
# Why not clobber an existing home: OAuth refresh ROTATES tokens. The
# runbook persists ~/.codex in a named docker volume across --rm runs; the
# volume's refreshed state is fresher than the host seed, so an existing
# auth.json is preferred. After a host re-login, force a reseed with
# CODEX_SEED_FORCE=1.
#
# No-op when the seed dir is absent (host shakedown, credential-less
# dryrun service). Fails (exit 1) if a seed dir exists WITHOUT auth.json:
# a credential-less live launch should die here, not burn S1 work first.
set -euo pipefail

SEED="${CODEX_SEED_DIR:-$HOME/.codex-seed}"
if [ ! -d "$SEED" ]; then
    exit 0
fi

if [ ! -f "$SEED/auth.json" ]; then
    echo "seed_codex_home: $SEED exists but holds no auth.json — refusing" >&2
    echo "  (mount it: -v \"\$HOME/.codex/auth.json:/home/labuser/.codex-seed/auth.json:ro\")" >&2
    exit 1
fi

mkdir -p "$HOME/.codex"
# a volume-mounted dir may refuse chmod; the auth FILE's 600 is what
# matters, and the image pre-creates the dir 700-owned by labuser
chmod 700 "$HOME/.codex" 2>/dev/null || true
if [ -f "$HOME/.codex/auth.json" ] && [ "${CODEX_SEED_FORCE:-0}" != "1" ]; then
    echo "seed_codex_home: keeping existing ~/.codex/auth.json (persisted volume state is fresher than the host seed; CODEX_SEED_FORCE=1 overrides)"
    exit 0
fi
cp "$SEED/auth.json" "$HOME/.codex/auth.json"
chmod 600 "$HOME/.codex/auth.json"
echo "seed_codex_home: seeded auth.json into writable ~/.codex"
