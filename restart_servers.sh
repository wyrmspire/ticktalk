#!/bin/bash
# restart_servers.sh: Kill all relevant processes and start servers in the correct order using .venv

set -e

# Kill all Python and Caddy processes
pkill -f '/workspaces/ticktalk/.venv/bin/python' || true
pkill -f 'python' || true
pkill -f 'caddy' || true
sleep 2

# Start servers in the correct order
bash start_servers.sh

echo "All servers restarted successfully."
