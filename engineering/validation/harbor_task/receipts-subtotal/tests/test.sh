#!/bin/bash
# The verifier: the task's own tests, and the reward Harbor reads.
# Copied to /tests/test.sh and run from the working directory.

set -u

python3 -m pip install --quiet --break-system-packages pytest 2>/dev/null \
  || python3 -m pip install --quiet pytest

cd /app
python3 -m pytest -rA /tests/test_outputs.py
status=$?

mkdir -p /logs/verifier
if [ $status -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
