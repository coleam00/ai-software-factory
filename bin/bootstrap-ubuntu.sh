#!/usr/bin/env bash
# Put everything the factory needs on a fresh Ubuntu box. Any provider.
#
#   curl -fsSL https://raw.githubusercontent.com/coleam00/ai-software-factory/main/bin/bootstrap-ubuntu.sh | bash
#
# Idempotent: run it twice and the second run changes nothing. Installs, as root:
#   git, python3, curl, unzip     stock packages
#   gh                            from GitHub's apt repo. Ubuntu's own gh is too old: its
#                                 `gh pr edit --add-label` uses a retired GraphQL field and
#                                 the factory's open-pr step fails on it.
#   caddy                         the reverse proxy the deploy step puts the app behind
#   bun                           builds and runs Archon, the workflow engine
#   uv                            every factory script node runs under it
#   claude                        Claude Code, the coding agent (swap for your own)
#
# What it deliberately does NOT do: log anything in. GitHub and the coding agent both
# need a browser once, on your laptop (`gh auth login` device code, `claude setup-token`).
# Those two commands are the only part of a server setup a person has to do.
#
# On Hostinger this file can be saved as a post-install script so a new VPS comes up with
# all of it already there. On any other host, ssh in and run the line above.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root (or with sudo): the apt steps need it" >&2
  exit 1
fi

say() { printf '\n==> %s\n' "$*"; }

say "apt: git, python3, curl, unzip, caddy"
apt-get update -qq
apt-get install -y -qq git python3 curl wget unzip ca-certificates gnupg caddy >/dev/null

say "gh from GitHub's apt repo (Ubuntu's is too old for the factory)"
if ! command -v gh >/dev/null 2>&1 || ! [ -f /etc/apt/sources.list.d/github-cli.list ]; then
  mkdir -p -m 755 /etc/apt/keyrings
  wget -qO- https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    > /etc/apt/keyrings/githubcli-archive-keyring.gpg
  chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list
  apt-get update -qq
  apt-get install -y -qq gh >/dev/null
fi

say "bun"
if ! [ -x "$HOME/.bun/bin/bun" ]; then
  curl -fsSL https://bun.sh/install | bash >/dev/null
fi

say "uv"
if ! [ -x "$HOME/.local/bin/uv" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
fi

say "Claude Code"
if ! [ -x "$HOME/.local/bin/claude" ]; then
  curl -fsSL https://claude.ai/install.sh | bash >/dev/null
fi

say "PATH for future shells"
PROFILE="$HOME/.bashrc"
LINE='export PATH="$HOME/.bun/bin:$HOME/.local/bin:$PATH"'
grep -qF '.bun/bin' "$PROFILE" 2>/dev/null || echo "$LINE" >> "$PROFILE"
git config --global init.defaultBranch main >/dev/null 2>&1 || true

export PATH="$HOME/.bun/bin:$HOME/.local/bin:$PATH"
say "installed"
printf '  %-8s %s\n' git "$(git --version | cut -d' ' -f3)" \
  python3 "$(python3 --version | cut -d' ' -f2)" \
  gh "$(gh --version | head -1 | cut -d' ' -f3)" \
  caddy "$(caddy version | cut -d' ' -f1)" \
  bun "$(bun --version)" \
  uv "$(uv --version | cut -d' ' -f2)" \
  claude "$(claude --version 2>/dev/null | cut -d' ' -f1)"
cat <<'EOF'

Next, the two logins only you can do:
  gh auth login --hostname github.com --git-protocol https --web
  gh auth setup-git
  # on your laptop:  claude setup-token
  # here:            echo 'export CLAUDE_CODE_OAUTH_TOKEN=<token>' >> ~/.bashrc && source ~/.bashrc
Then, in your repo:
  git clone https://github.com/coleam00/ai-software-factory ~/ai-software-factory
  python3 ~/ai-software-factory/bin/factory.py init
EOF
