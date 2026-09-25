#!/bin/bash
set -u
mkdir -p /logs/verifier
if python3 -c "import sys; sys.path.insert(0, '/app'); from calc import double; assert double(2) == 4 and double(-3) == -6"; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
