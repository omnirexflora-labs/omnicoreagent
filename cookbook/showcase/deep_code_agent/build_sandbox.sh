#!/bin/bash
# deep_coder/build_sandbox.sh
set -e

echo "Building deepcoder-sandbox image..."
docker build -f Dockerfile.sandbox -t deepcoder-sandbox:1.0 .

echo "Tagging latest..."
docker tag deepcoder-sandbox:1.0 deepcoder-sandbox:latest

echo "Image built successfully."