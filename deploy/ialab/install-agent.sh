#!/usr/bin/env bash
# install-agent.sh — idempotent install of the Node Agent on ialab.
#
# Run from the repo root on ialab:
#   bash deploy/ialab/install-agent.sh
#
# Re-running is safe: it updates code, re-syncs deps, and restarts the service.
# The env file (/etc/ai-platform/agent.env) is never overwritten if it already
# exists, so real credentials survive re-installs.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INSTALL_DIR="/opt/ai-platform/agent"
ENV_FILE="/etc/ai-platform/agent.env"
UNIT_FILE="/etc/systemd/system/node-agent.service"
AGENT_USER="${AGENT_USER:-$(whoami)}"

echo "→ installing node-agent (user=$AGENT_USER)"

# ── 1. copy agent source ──────────────────────────────────────────────────────
sudo mkdir -p "$INSTALL_DIR"
sudo rsync -a --delete "$REPO_ROOT/agent/" "$INSTALL_DIR/"
sudo chown -R "$AGENT_USER:$AGENT_USER" "$INSTALL_DIR"

# ── 2. sync python dependencies into .venv ────────────────────────────────────
cd "$INSTALL_DIR"
uv sync --frozen --python 3.12

# ── 3. create env file (once) ─────────────────────────────────────────────────
if [ ! -f "$ENV_FILE" ]; then
    sudo mkdir -p "$(dirname "$ENV_FILE")"
    sudo cp "$SCRIPT_DIR/agent.env.example" "$ENV_FILE"
    sudo chmod 600 "$ENV_FILE"
    sudo chown root:root "$ENV_FILE"
    echo ""
    echo "  ⚠  $ENV_FILE created from template."
    echo "     Fill in real values before the service will work:"
    echo "       sudo \$EDITOR $ENV_FILE"
    echo "     Then re-run this script (or: systemctl restart node-agent)"
    echo ""
else
    echo "  ✓ $ENV_FILE already exists — not overwritten"
fi

# ── 4. install systemd unit ───────────────────────────────────────────────────
sudo sed "s/__AGENT_USER__/$AGENT_USER/g" \
    "$SCRIPT_DIR/node-agent.service" \
    | sudo tee "$UNIT_FILE" > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable node-agent
sudo systemctl restart node-agent

echo ""
echo "✓ node-agent installed and started"
echo "  status:  systemctl status node-agent"
echo "  logs:    journalctl -u node-agent -f"
