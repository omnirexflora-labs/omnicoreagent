# Docker Sandboxes — a possible provider, deferred

Status: noted 2026-09-25, **not planned yet**. The maintainer's decision: the
existing `docker` provider serves users well, and Docker's new sandbox API is
marked experimental; come back to it when it is not.

## What it is

Docker Sandboxes run an agent's work in a **microVM** with its own kernel and
its own Docker daemon — locally (needs KVM or a hypervisor), and since
2026-09-24 in Docker's cloud as **Docker Cloud Sandboxes**, generally available
and billed per second.

- Announcement, 2026-09-24:
  <https://www.docker.com/blog/introducing-cloud-sandboxes-start-on-your-laptop-finish-in-the-cloud/>
- Kit Spec v3 (network rules, credentials, MCP servers and hooks declared in an
  OCI image), same day: <https://www.docker.com/blog/docker-sandbox-kit-spec/>
- Security model (deny-by-default egress proxy; credentials injected by a
  proxy outside the VM, never inside it): <https://docs.docker.com/ai/sandboxes/security/>
- API and TypeScript SDK, **experimental**, cloud only, no Python SDK:
  <https://docs.docker.com/ai/sandboxes-api/>

## How it compares with our `docker` provider

| | `docker` (today) | Docker Sandboxes |
|---|---|---|
| Boundary | a container on the user's Docker Engine | a microVM |
| Runs | on the user's machine | locally or in Docker's cloud |
| Secrets | kept out of the sandbox | injected by a proxy outside the VM |
| Network | none unless the policy allows | deny-by-default proxy, rules by host, path, method |
| Driven by | the Docker SDK | the `sbx` CLI; a REST API for the cloud |
| Cost | free | local free; cloud from $0.07/hour |

## If we build it

A new provider (`docker_sandbox`), complementing `docker`, not replacing it:
the REST API over httpx for the cloud, shaped like the E2B, Daytona and Modal
providers (create, wait until running, exec, copy in and out, stop, an explicit
TTL); `sbx` locally, without a mount, so files still move in and out as they do
today. Our network policy would map to its proxy rules, and secrets would use
its proxy rather than the environment.

Wait for: the API to leave experimental, and a stable way to exec in the
sandbox's own filesystem (the docs warn `docker exec` inside a cloud sandbox can
land in the VM rather than the target container).
