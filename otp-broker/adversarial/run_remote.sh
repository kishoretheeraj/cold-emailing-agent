#!/bin/bash
# usage: run_remote.sh <script.py> [args]   -- pushes adversarial/ + broker source, runs on the Beelink
set -e
D=$(cd "$(dirname "$0")" && pwd)
SRC=$(cd "$D/.." && pwd)
"$D/bx.sh" 'mkdir -p ~/otp-adv/src ~/otp-adv/tests ~/otp-adv/results'
tar -C "$SRC" -cf - main.py broker.py imap_client.py extract.py store.py | "$D/bx.sh" 'tar -C ~/otp-adv/src -xf -'
tar -C "$D" -cf - --exclude=__pycache__ --exclude='*.sh' . | "$D/bx.sh" 'tar -C ~/otp-adv/tests -xf -'
S=$1; shift
"$D/bx.sh" "cd ~/otp-adv/tests && PYTHONPATH=\$HOME/otp-adv/src:\$HOME/otp-adv/tests ~/otp-adv/venv/bin/python -u $S $*"
