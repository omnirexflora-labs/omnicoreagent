#!/bin/bash
# The verifier: the answer file, and the reward Harbor reads.
set -u
mkdir -p /logs/verifier
if python3 - <<'PY'
import json, sys
try:
    answer = json.load(open("/app/answer.json"))
except Exception as exc:
    sys.exit(f"no readable /app/answer.json: {exc}")
expected = {"zeldovia": 0.137, "kestria": 0.0625}
for country, rate in expected.items():
    got = answer.get(country)
    if not isinstance(got, (int, float)) or abs(got - rate) > 1e-9:
        sys.exit(f"{country}: expected {rate}, got {got!r}")
print("both rates are right")
PY
then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
