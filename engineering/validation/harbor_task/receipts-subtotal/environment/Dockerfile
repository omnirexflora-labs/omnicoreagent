FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# python3 and pip are the task's own tooling: its tests are pytest, and an
# agent installed into this container needs somewhere to install itself.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-pip python3-venv git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY receipts.py /app/receipts.py
COPY test_receipts.py /app/test_receipts.py
