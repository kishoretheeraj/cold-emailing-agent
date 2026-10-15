#!/bin/bash
# usage: bx.sh '<remote command>'
exec ssh -F /dev/null -i /home/hatch/.ssh/id_ed25519 -o StrictHostKeyChecking=accept-new \
  -o ProxyCommand="/home/hatch/workspace/bin/ts_tunnel_proxy.py %h %p" -o ConnectTimeout=30 \
  kishore@100.77.193.40 "$@"
