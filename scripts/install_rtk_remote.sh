#!/usr/bin/env bash
#
# install_rtk_remote.sh — install rtk (Rust Token Killer) on a Linux host and
#                         wire it into Claude Code's Bash hook.
#
# Run it ON the target machine, or pipe it over ssh:
#     ssh user@host 'bash -s' < scripts/install_rtk_remote.sh
#
# WHY A SCRIPT RATHER THAN A ONE-LINER
# ------------------------------------
# Three things make the naive `dpkg -i rtk_amd64.deb` wrong on some hosts:
#
#   * There is NO arm64 .deb in the release. An arm64 server (Pi, Ampere, an
#     ARM VPS) has to take the aarch64 tarball instead, so the installer must
#     branch on `uname -m` rather than assume amd64.
#   * `rtk` collides with reachingforthejack/rtk (Rust Type Kit). Installing
#     over that one leaves a binary that answers `--version` but has no `gain`
#     subcommand, which looks like a broken install rather than a wrong one.
#   * The hook is what actually saves tokens. A binary on PATH with no
#     PreToolUse hook in settings.json does nothing at all.
#
# Everything here is idempotent: re-running upgrades in place and will not
# duplicate the hook entry.
set -euo pipefail

VERSION="0.44.2"
BASE="https://github.com/rtk-ai/rtk/releases/download/v${VERSION}"

log()  { printf '  %s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

echo
log "host   : $(hostname)"
log "distro : $( . /etc/os-release 2>/dev/null && echo "$PRETTY_NAME" || echo unknown )"
log "arch   : $(uname -m)"
echo

# --- name-collision check ----------------------------------------------------
# Do this BEFORE installing: if the wrong rtk is present, say so plainly rather
# than layering ours on top and leaving two candidates on PATH.
if command -v rtk >/dev/null 2>&1; then
  cur=$(rtk --version 2>/dev/null | head -1 || true)
  if rtk gain --help >/dev/null 2>&1; then
    log "existing rtk: ${cur:-unknown} (correct project)"
  else
    warn "an 'rtk' is already installed that has no 'gain' command:"
    warn "    $(command -v rtk)  ->  ${cur:-no version output}"
    warn "this is probably reachingforthejack/rtk (Rust Type Kit)."
    warn "remove it first, or ours will land behind it on PATH."
  fi
fi

# --- install -----------------------------------------------------------------
SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

case "$(uname -m)" in
  x86_64|amd64)
    log "installing the amd64 .deb"
    curl -fsSL "$BASE/rtk_${VERSION}-1_amd64.deb" -o "$tmp/rtk.deb" \
      || die "download failed"
    $SUDO dpkg -i "$tmp/rtk.deb" 2>/dev/null \
      || { log "fixing dependencies"; $SUDO apt-get -y -f install; }
    ;;
  aarch64|arm64)
    # No arm64 .deb exists in this release — tarball into /usr/local/bin.
    log "no arm64 .deb published; installing the aarch64 tarball"
    curl -fsSL "$BASE/rtk-aarch64-unknown-linux-gnu.tar.gz" -o "$tmp/rtk.tgz" \
      || die "download failed"
    tar -xzf "$tmp/rtk.tgz" -C "$tmp"
    bin=$(find "$tmp" -type f -name rtk -perm -u+x | head -1)
    [ -n "$bin" ] || die "no rtk binary inside the tarball"
    $SUDO install -m 0755 "$bin" /usr/local/bin/rtk
    ;;
  *) die "unsupported architecture: $(uname -m)" ;;
esac

command -v rtk >/dev/null 2>&1 || die "rtk is not on PATH after install"
ok "installed: $(rtk --version 2>/dev/null | head -1)"

# The real acceptance test. `--version` only proves A binary exists; `gain` is
# the subcommand the collision package does not have.
if rtk gain >/dev/null 2>&1 || rtk gain --help >/dev/null 2>&1; then
  ok "'rtk gain' works — this is the right rtk"
else
  die "'rtk gain' failed — wrong rtk is winning on PATH: $(command -v rtk)"
fi

# --- wire the Claude Code hook ----------------------------------------------
# Without this the binary is inert: the savings come from the PreToolUse hook
# rewriting Bash commands, not from rtk merely being installed.
SETTINGS="$HOME/.claude/settings.json"
mkdir -p "$HOME/.claude"
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
cp "$SETTINGS" "$SETTINGS.bak.$(date +%Y%m%d%H%M%S)"

python3 - "$SETTINGS" <<'PY'
import json, sys

path = sys.argv[1]
try:
    with open(path) as fh:
        cfg = json.load(fh)
except (json.JSONDecodeError, OSError) as exc:
    print(f"  ! could not parse {path} ({exc}) — leaving it alone")
    sys.exit(1)

hooks  = cfg.setdefault("hooks", {})
pre    = hooks.setdefault("PreToolUse", [])
WANTED = "rtk hook claude"

# Idempotent: a re-run must not append a second identical hook.
for entry in pre:
    for h in entry.get("hooks", []):
        if h.get("command") == WANTED:
            print("  hook already present — nothing to change")
            sys.exit(0)

pre.append({"matcher": "Bash",
            "hooks": [{"type": "command", "command": WANTED}]})
with open(path, "w") as fh:
    json.dump(cfg, fh, indent=2)
print("  hook added to PreToolUse (matcher: Bash)")
PY

echo
ok "done — restart Claude Code on this host for the hook to load"
log "verify with:  rtk --version && rtk gain"
echo
