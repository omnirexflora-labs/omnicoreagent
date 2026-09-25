FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip python3-venv git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
