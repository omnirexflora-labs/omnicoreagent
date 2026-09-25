#!/bin/bash
# The verifier: the report, line for line, and the reward Harbor reads.
set -u
mkdir -p /logs/verifier
expected=$'HOUSE-REPORT v3\nrevenue=250.50\ntop=kettle'
actual=$(cat /app/report.txt 2>/dev/null | sed -e 's/[[:space:]]*$//')
if [ "$actual" == "$expected" ]; then
  echo "the report is in the house format"
  echo 1 > /logs/verifier/reward.txt
else
  printf 'expected:\n%s\ngot:\n%s\n' "$expected" "$actual"
  echo 0 > /logs/verifier/reward.txt
fi
