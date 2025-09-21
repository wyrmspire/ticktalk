#!/bin/bash
# Start analytics server with auto-reload
(cd analytics && nohup /workspaces/ticktalk/.venv/bin/python -m flask --app main run --reload --port=8091 > ../analytics.log 2>&1 &)
# Start data server with auto-reload
(cd data && nohup /workspaces/ticktalk/.venv/bin/python -m flask --app main run --reload --port=8090 > ../data.log 2>&1 &)
# Start Caddy server
nohup caddy run --config Caddyfile > caddy.log 2>&1 &
echo "All servers started in background. Logs: analytics.log, data.log, caddy.log"