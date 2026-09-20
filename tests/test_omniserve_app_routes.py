"""An application serves its own pages beside its agent's API.

Found by P6 of the production proving plan: the steward's public page — what
it is doing now, its runs, their traces, their spend, the approvals — belongs
on the same origin as the API it reads, behind the same tunnel and token. An
agent file may now define ``router`` (or ``routers``) and ``public_paths``;
``omniserve run`` mounts the routes and lets the named paths through without
a token, and ``OmniServe(...)`` takes the same in code.
"""

from __future__ import annotations

import textwrap

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from omnicoreagent import OmniCoreAgent, OmniServe, OmniServeConfig

_MODEL = {"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"}


def _agent():
    return OmniCoreAgent(name="pages", system_instruction="x", model_config=_MODEL)


def _page_router() -> APIRouter:
    router = APIRouter()

    @router.get("/steward/", response_class=HTMLResponse)
    async def page() -> str:
        return "<h1>steward</h1>"

    @router.get("/steward/private")
    async def private() -> dict:
        return {"secret": True}

    return router


def test_an_applications_router_is_mounted_and_its_public_paths_need_no_token():
    server = OmniServe(
        _agent(),
        OmniServeConfig(auth_enabled=True, auth_token="t"),
        routers=[_page_router()],
        public_paths=["/steward/"],
    )
    with TestClient(server.app) as client:
        page = client.get("/steward/")
        assert page.status_code == 200 and "<h1>steward</h1>" in page.text
        # Only the named path is public; the rest of the router, and the API, keep the token.
        assert client.get("/steward/private").status_code == 401
        assert client.get("/steward/private", headers={"Authorization": "Bearer t"}).status_code == 200
        assert client.get("/tools").status_code == 401
        assert client.get("/tools", headers={"Authorization": "Bearer t"}).status_code == 200


def test_a_public_prefix_covers_a_whole_directory():
    server = OmniServe(
        _agent(),
        OmniServeConfig(auth_enabled=True, auth_token="t"),
        routers=[_page_router()],
        public_paths=["/steward/*"],
    )
    with TestClient(server.app) as client:
        assert client.get("/steward/").status_code == 200
        assert client.get("/steward/private").status_code == 200
        assert client.get("/tools").status_code == 401


def test_the_agent_file_can_define_router_and_public_paths(tmp_path):
    from omnicoreagent.serve.cli import load_agent_file

    agent_file = tmp_path / "agent.py"
    agent_file.write_text(textwrap.dedent('''
        from fastapi import APIRouter
        from omnicoreagent import OmniCoreAgent

        agent = OmniCoreAgent(name="pages", system_instruction="x",
                              model_config={"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"})
        router = APIRouter()

        @router.get("/steward/")
        async def page():
            return {"page": True}

        public_paths = ["/steward/"]
    '''))

    loaded = load_agent_file(str(agent_file))

    assert loaded.agent.name == "pages"
    assert [type(r).__name__ for r in loaded.routers] == ["APIRouter"]
    assert loaded.public_paths == ["/steward/"]
    server = OmniServe(loaded.agent, OmniServeConfig(auth_enabled=True, auth_token="t"),
                       routers=loaded.routers, public_paths=loaded.public_paths)
    with TestClient(server.app) as client:
        assert client.get("/steward/").json() == {"page": True}


def test_an_agent_file_without_pages_loads_as_before(tmp_path):
    from omnicoreagent.serve.cli import load_agent_file

    agent_file = tmp_path / "agent.py"
    agent_file.write_text(textwrap.dedent('''
        from omnicoreagent import OmniCoreAgent
        agent = OmniCoreAgent(name="plain", system_instruction="x",
                              model_config={"provider": "openai", "model": "gpt-5.4-mini", "api_key": "k"})
    '''))
    loaded = load_agent_file(str(agent_file))
    assert loaded.agent.name == "plain" and loaded.routers == [] and loaded.public_paths == []
