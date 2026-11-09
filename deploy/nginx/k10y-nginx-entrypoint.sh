#!/bin/bash
# Nginx (through the official image's entrypoint, so its template handling still works) and the k10y agent.
# The container stops when either stops.
set -m
/docker-entrypoint.sh nginx -g 'daemon off;' &
nginx_pid=$!
/opt/k10y/bin/k10y-agent serve &
agent_pid=$!
trap 'kill -TERM $nginx_pid $agent_pid 2>/dev/null' TERM INT
wait -n $nginx_pid $agent_pid
status=$?
kill -TERM $nginx_pid $agent_pid 2>/dev/null
wait
exit $status
