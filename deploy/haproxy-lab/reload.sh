#!/bin/sh
# Called by the Data Plane API after a config change. Saves the servers' runtime state (drain,
# maintenance, weight) so the new HAProxy process keeps it, then asks the master to reload and fails
# unless the new configuration actually loaded.
echo "@1 show servers state" | nc -w 3 haproxy 9999 > /var/run/haproxy/server-state.tmp \
  && mv /var/run/haproxy/server-state.tmp /var/run/haproxy/server-state
out=$(echo reload | nc -w 5 haproxy 9999)
echo "$out"
echo "$out" | grep -q '^Success=1'
