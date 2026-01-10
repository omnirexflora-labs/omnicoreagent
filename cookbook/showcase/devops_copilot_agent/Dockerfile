# FROM python:3.11-slim

# RUN apt-get update && apt-get install -y \
#     bash curl wget jq tree procps net-tools iputils-ping htop \
#     && rm -rf /var/lib/apt/lists/*

# # Create user + dirs + make /app writable
# RUN useradd -m -u 1000 copilot && \
#     mkdir -p /workspace /logs /app && \
#     chown -R copilot:copilot /workspace /logs /app

# WORKDIR /app
# COPY requirements.txt .
# RUN pip install --no-cache-dir -r requirements.txt
# COPY --chown=copilot:copilot . .

# # PRE-CREATE omnicoreagent.log
# RUN touch /app/omnicoreagent.log && \
#     chown copilot:copilot /app/omnicoreagent.log

# USER copilot
# CMD ["python", "devops_copilot_agent.py"]

# Dockerfile for OmniDevOpsCopilot with Docker & Kubernetes support
# =======================================================================
FROM python:3.11-slim

# Environment variables
ENV DEBIAN_FRONTEND=noninteractive \
    KUBECTL_VERSION=v1.28.4 \
    HELM_VERSION=v3.13.2

# =======================================================================
# 1. Install base system tools
# =======================================================================
RUN apt-get update && apt-get install -y \
    # Core tools
    bash curl wget jq tree procps net-tools iputils-ping htop vim \
    # Build essentials (needed for some tools)
    ca-certificates gnupg lsb-release apt-transport-https \
    # Network & security tools
    dnsutils telnet traceroute \
    # System monitoring
    sysstat iotop iftop \
    # File tools
    file rsync unzip zip \
    # Git (for potential repo operations)
    git \
    && rm -rf /var/lib/apt/lists/*

# =======================================================================
# 2. Install Docker CLI (flexible version detection)
# =======================================================================
RUN mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
    chmod a+r /etc/apt/keyrings/docker.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" | \
    tee /etc/apt/sources.list.d/docker.list > /dev/null && \
    apt-get update && \
    apt-get install -y docker-ce-cli docker-compose-plugin && \
    rm -rf /var/lib/apt/lists/*

# =======================================================================
# 3. Install kubectl (Kubernetes CLI)
# =======================================================================
RUN curl -LO "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" && \
    curl -LO "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl.sha256" && \
    echo "$(cat kubectl.sha256)  kubectl" | sha256sum --check && \
    install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && \
    rm kubectl kubectl.sha256

# =======================================================================
# 4. Install Helm (Kubernetes package manager)
# =======================================================================
RUN curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

# =======================================================================
# 5. Install additional DevOps tools
# =======================================================================

# Install docker-compose standalone (if compose plugin isn't enough)
RUN curl -L "https://github.com/docker/compose/releases/download/v2.23.3/docker-compose-$(uname -s)-$(uname -m)" \
    -o /usr/local/bin/docker-compose && \
    chmod +x /usr/local/bin/docker-compose

# Install yq (YAML processor)
RUN wget https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64 -O /usr/local/bin/yq && \
    chmod +x /usr/local/bin/yq

# Install k9s (Kubernetes TUI) - optional but useful
RUN wget https://github.com/derailed/k9s/releases/download/v0.28.2/k9s_Linux_amd64.tar.gz && \
    tar -xzf k9s_Linux_amd64.tar.gz -C /usr/local/bin k9s && \
    rm k9s_Linux_amd64.tar.gz

# Install stern (multi-pod log tailing)
RUN wget https://github.com/stern/stern/releases/download/v1.28.0/stern_1.28.0_linux_amd64.tar.gz && \
    tar -xzf stern_1.28.0_linux_amd64.tar.gz -C /usr/local/bin stern && \
    rm stern_1.28.0_linux_amd64.tar.gz

# Install kubectx and kubens (context switching)
RUN wget https://raw.githubusercontent.com/ahmetb/kubectx/master/kubectx -O /usr/local/bin/kubectx && \
    wget https://raw.githubusercontent.com/ahmetb/kubectx/master/kubens -O /usr/local/bin/kubens && \
    chmod +x /usr/local/bin/kubectx /usr/local/bin/kubens

# =======================================================================
# 6. Create user + directories
# =======================================================================
RUN useradd -m -u 1000 copilot && \
    mkdir -p /workspace /logs /app /home/copilot/.kube /home/copilot/.docker && \
    chown -R copilot:copilot /workspace /logs /app /home/copilot

# =======================================================================
# 7. Setup Python application
# =======================================================================
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=copilot:copilot . .

# Pre-create log files
RUN touch /app/omnicoreagent.log /app/audit.log && \
    chown copilot:copilot /app/omnicoreagent.log /app/audit.log

# =======================================================================
# 8. Setup permissions for Docker socket access (when mounted)
# =======================================================================
# Add copilot user to docker group (gid 999 is common for docker group)
RUN groupadd -g 999 docker 2>/dev/null || true && \
    usermod -aG docker copilot 2>/dev/null || true

# Also add gid 998 as alternative (some systems use this)
RUN groupadd -g 998 dockeralt 2>/dev/null || true && \
    usermod -aG dockeralt copilot 2>/dev/null || true

# =======================================================================
# 9. Health check
# =======================================================================
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/metrics || exit 1

# =======================================================================
# 10. Labels and metadata
# =======================================================================
LABEL maintainer="OmniDevOpsCopilot Team" \
      description="AI-powered DevOps assistant with Docker and Kubernetes support" \
      version="2.0.0" \
      org.opencontainers.image.source="https://github.com/Abiorh001/omnicoreagent"

# =======================================================================
# 11. Switch to non-root user
# =======================================================================
USER copilot

# Set environment for kubectl and docker
ENV KUBECONFIG=/home/copilot/.kube/config \
    DOCKER_CONFIG=/home/copilot/.docker

# =======================================================================
# 12. Startup
# =======================================================================
CMD ["python", "devops_copilot_agent.py"]