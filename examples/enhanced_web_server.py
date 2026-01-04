#!/usr/bin/env python3
"""
Enhanced OmniCoreAgent Web Server - Professional Showcase Interface
Clean FastAPI app using lifespan with separate main agent and background agent services.
"""

import asyncio
import json
import os
from datetime import datetime
from typing import AsyncGenerator, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from contextlib import asynccontextmanager

# OmniCoreAgent imports
from omnicoreagent import (
    OmniCoreAgent,
    MemoryRouter,
    EventRouter,
    BackgroundAgentManager,
    ToolRegistry,
    logger,
)

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(SCRIPT_DIR, "static")
TEMPLATES_DIR = os.path.join(SCRIPT_DIR, "templates")


# Data Models
class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class BackgroundAgentRequest(BaseModel):
    agent_id: str
    query: str
    schedule: Optional[str] = None


class TaskUpdateRequest(BaseModel):
    agent_id: str
    query: str


class AgentConfigRequest(BaseModel):
    name: str
    system_instruction: str
    llm_config: dict
    agent_config: dict


def build_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()

    @registry.register_tool("calculate_area")
    def calculate_area(length: float, width: float) -> str:
        """Calculate area of a rectangle

        Parameters
        ----------
        length : float

        width : float


        Returns
        -------
        str
            Formatted area string
        """
        return f"Area of rectangle ({length} x {width}): {length * width} square units"

    @registry.register_tool("calculate_perimeter")
    def calculate_perimeter(length: float, width: float) -> str:
        """Calculate perimeter of a rectangle"""

        return (
            f"Perimeter of rectangle ({length} x {width}): {2 * (length + width)} units"
        )

    @registry.register_tool("format_text")
    def format_text(text: str, style: str = "normal") -> str:
        """Format text in various styles"""
        if style == "uppercase":
            return text.upper()
        if style == "lowercase":
            return text.lower()
        if style == "title":
            return text.title()
        if style == "reverse":
            return text[::-1]
        return text

    @registry.register_tool("word_count")
    def word_count(text: str) -> str:
        """Count number of words in text"""
        return f"Word count: {len(text.split())} words"

    @registry.register_tool("system_info")
    def system_info() -> str:
        """Get basic system information of the server"""
        import platform
        import time

        return (
            "System Information:\n"
            f"• OS: {platform.system()} {platform.release()}\n"
            f"• Architecture: {platform.machine()}\n"
            f"• Python Version: {platform.python_version()}\n"
            f"• Current Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    @registry.register_tool("analyze_numbers")
    def analyze_numbers(numbers: str) -> str:
        """Analyze a list of comma-separated numbers"""
        try:
            values = [float(x.strip()) for x in numbers.split(",") if x.strip()]
            if not values:
                return "No numbers provided"
            total = sum(values)
            avg = total / len(values)
            return (
                "Number Analysis:\n"
                f"• Count: {len(values)} numbers\n"
                f"• Sum: {total}\n"
                f"• Average: {avg:.2f}\n"
                f"• Min: {min(values)}\n"
                f"• Max: {max(values)}"
            )
        except Exception as exc:
            return f"Error analyzing numbers: {exc}"

    return registry


MCP_TOOLS = [
    {
        "name": "filesystem",
        "command": "npx",
        "args": [
            "-y",
            "@modelcontextprotocol/server-filesystem",
            "/home/abiorh/Desktop",
            "/home/abiorh/ai/",
        ],
    }
]


class MainAgentService:
    def __init__(self) -> None:
        self.agent: Optional[OmniCoreAgent] = None
        self.local_tools: ToolRegistry = build_tool_registry()
        self.memory_router: Optional[MemoryRouter] = None
        self.event_router: Optional[EventRouter] = None
        self._mcp_servers_connected: bool = False

    async def initialize(self) -> None:
        self.memory_router = MemoryRouter(memory_store_type="in_memory")
        self.event_router = EventRouter(event_store_type="in_memory")
        self.agent = OmniCoreAgent(
            name="enhanced_web_agent",
            system_instruction=(
                "You are an advanced AI assistant with access to multiple tools and capabilities.\n"
                "You can perform mathematical calculations, text processing, system analysis, and data analysis.\n"
                "Always provide clear, helpful responses and use the appropriate tools when needed."
            ),
            model_config={"provider": "openai", "model": "gpt-4.1", "temperature": 0.3},
            agent_config={"max_steps": 15, "tool_call_timeout": 60},
            embedding_config={
                "provider": "voyage",
                "model": "voyage-3.5",
                "dimensions": 1024,
                "encoding_format": "base64",
            },
            mcp_tools=MCP_TOOLS,
            local_tools=self.local_tools,
            memory_router=self.memory_router,
            event_router=self.event_router,
            debug=True,
        )

        # Connect to MCP servers
        await self.agent.connect_mcp_servers()
        self._mcp_servers_connected = True
        logger.info("Initialized MainAgentService with OmniCoreAgent")

    async def run(self, query: str, session_id: str) -> dict:
        if not self.agent:
            raise HTTPException(status_code=503, detail="Agent not initialized")

        return await self.agent.run(query=query, session_id=session_id)

    def switch_memory_backend(self, backend: str) -> None:
        # backend: "in_memory" | "database" | "redis" | "mongodb"
        if self.agent:
            self.agent.swith_memory_store(backend)

    def switch_event_backend(self, backend: str) -> None:
        # backend: "in_memory" | "redis_stream"
        if self.agent:
            self.agent.switch_event_store(backend)


class BackgroundAgentService:
    def __init__(self, memory_router: MemoryRouter, event_router: EventRouter) -> None:
        self.manager = BackgroundAgentManager(
            memory_router=memory_router, event_router=event_router
        )

    async def create(self, agent_config: dict) -> dict:
        # BackgroundAgentManager.create_agent is now async
        return await self.manager.create_agent(agent_config)

    def list(self):
        # Returns list of agent IDs
        return self.manager.list_agents()

    def start_manager(self) -> None:
        # Ensure scheduler is running
        self.manager.start()

    def shutdown_manager(self) -> None:
        self.manager.shutdown()

    def resume_agent(self, agent_id: str) -> None:
        # Resume scheduling of a specific agent
        self.manager.resume_agent(agent_id)

    def pause_agent(self, agent_id: str) -> None:
        # Pause scheduling of a specific agent
        self.manager.pause_agent(agent_id)

    def start_agent(self, agent_id: str) -> None:
        self.manager.start_agent(agent_id)

    def stop_agent(self, agent_id: str) -> None:
        self.manager.stop_agent(agent_id)

    def update_task_config(self, agent_id: str, task_config: dict) -> bool:
        return self.manager.update_task_config(agent_id, task_config)

    def remove_task(self, agent_id: str) -> bool:
        return self.manager.remove_task(agent_id)

    def get_agent_status(self, agent_id: str):
        return self.manager.get_agent_status(agent_id)

    def get_manager_status(self):
        return self.manager.get_manager_status()

    async def connect_mcp(self, agent_id: str) -> None:
        agent = self.manager.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        await agent.connect_mcp_servers()


# FastAPI app with lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🔄 Initializing services (lifespan)...")
    main_service = MainAgentService()
    await main_service.initialize()
    bg_service = BackgroundAgentService(
        memory_router=main_service.memory_router, event_router=main_service.event_router
    )

    app.state.main_service = main_service
    app.state.bg_service = bg_service
    app.state.active_connections = []  # for WebSocket tracking only

    yield
    # cleanup on shutdown
    # cleanup the main agent mcp servers
    await app.state.main_service.agent.cleanup()


app = FastAPI(
    title="OmniCoreAgent Professional Interface",
    description="Comprehensive AI Agent Framework with MCP Client Capabilities",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static + templates
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


# Routes
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        "enhanced_omni_agent_interface.html", {"request": request}
    )


@app.post("/api/chat")
async def chat_endpoint(payload: ChatRequest, request: Request):
    if not hasattr(request.app.state, "main_service"):
        raise HTTPException(status_code=503, detail="Service not initialized")
    session_id = payload.session_id or "web_session_001"

    async def generate_response() -> AsyncGenerator[str, None]:
        try:
            result = await request.app.state.main_service.run(
                payload.message, session_id
            )
            response = result.get("response", "No response received")
            chunk_size = 80
            for i in range(0, len(response), chunk_size):
                chunk = response[i : i + chunk_size]
                yield f"data: {json.dumps({'type': 'chunk', 'content': chunk, 'session_id': session_id})}\n\n"
                await asyncio.sleep(0.03)
            yield f"data: {json.dumps({'type': 'complete', 'session_id': session_id})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'content': str(exc), 'session_id': session_id})}\n\n"

    return StreamingResponse(generate_response(), media_type="text/plain")


@app.post("/api/background/create")
async def create_background_agent(payload: BackgroundAgentRequest, request: Request):
    if not hasattr(request.app.state, "bg_service"):
        raise HTTPException(
            status_code=503, detail="Background service not initialized"
        )

    # derive interval seconds from schedule input
    def _parse_schedule_to_interval(sched: Optional[str]) -> Optional[int]:
        if not sched:
            return None
        try:
            # raw number -> seconds
            secs = int(str(sched).strip())
            return max(1, secs)
        except Exception:
            text = str(sched).lower().strip()
            import re

            m = re.search(r"(\d+)(?:\s*)(second|sec|s|minute|min|m|hour|hr|h)s?", text)
            if m:
                value = int(m.group(1))
                unit = m.group(2)
                if unit in ("second", "sec", "s"):
                    return max(1, value)
                if unit in ("minute", "min", "m"):
                    return max(1, value * 60)
                if unit in ("hour", "hr", "h"):
                    return max(1, value * 3600)
            # patterns like "every 5 minutes"
            m2 = re.search(
                r"every\s+(\d+)\s+(second|sec|s|minute|min|m|hour|hr|h)s?", text
            )
            if m2:
                value = int(m2.group(1))
                unit = m2.group(2)
                if unit in ("second", "sec", "s"):
                    return max(1, value)
                if unit in ("minute", "min", "m"):
                    return max(1, value * 60)
                if unit in ("hour", "hr", "h"):
                    return max(1, value * 3600)
        return None

    interval_seconds = _parse_schedule_to_interval(payload.schedule)

    agent_config = {
        "agent_id": payload.agent_id,
        "system_instruction": f"You are a background agent that performs the task: {payload.query}",
        "model_config": {"provider": "openai", "model": "gpt-4.1", "temperature": 0.3},
        "agent_config": {"max_steps": 10, "tool_call_timeout": 60},
        # interval must be at top-level for BackgroundOmniCoreAgent to pick it up
        "interval": interval_seconds if interval_seconds is not None else 3600,
        "task_config": {
            "query": payload.query,
            "schedule": payload.schedule or "immediate",
            "interval": interval_seconds,
            "max_retries": 2,
            "retry_delay": 30,
        },
        "mcp_tools": MCP_TOOLS,
        "local_tools": build_tool_registry(),
    }
    details = await request.app.state.bg_service.create(agent_config)
    # Ensure manager is running so scheduling occurs
    request.app.state.bg_service.start_manager()

    return {
        "status": "success",
        "agent_id": payload.agent_id,
        "message": "Background agent created",
        "details": details,
    }


@app.get("/api/background/list")
async def list_background_agents(request: Request):
    ids = request.app.state.bg_service.list()
    # enrich with minimal details
    detailed = []
    for agent_id in ids:
        status = request.app.state.bg_service.get_agent_status(agent_id) or {}
        # extract query from task_config if available
        task_cfg = status.get("task_config") or {}
        detailed.append(
            {
                "agent_id": agent_id,
                "query": task_cfg.get("query"),
                "is_running": status.get("is_running"),
                "scheduled": status.get("scheduled"),
                "schedule": task_cfg.get("schedule"),
                "interval": task_cfg.get("interval"),
                "session_id": request.app.state.bg_service.manager.get_agent_session_id(
                    agent_id
                ),
            }
        )
    return {"status": "success", "agents": detailed}


@app.post("/api/background/start")
async def start_background_agent(payload: BackgroundAgentRequest, request: Request):
    request.app.state.bg_service.start_agent(payload.agent_id)
    return {"status": "success", "message": f"Agent {payload.agent_id} started"}


@app.post("/api/background/stop")
async def stop_background_agent(payload: BackgroundAgentRequest, request: Request):
    request.app.state.bg_service.stop_agent(payload.agent_id)
    return {"status": "success", "message": f"Agent {payload.agent_id} stopped"}


@app.post("/api/background/pause")
async def pause_background_agent(payload: BackgroundAgentRequest, request: Request):
    request.app.state.bg_service.pause_agent(payload.agent_id)
    return {"status": "success", "message": f"Agent {payload.agent_id} paused"}


@app.post("/api/background/resume")
async def resume_background_agent(payload: BackgroundAgentRequest, request: Request):
    request.app.state.bg_service.resume_agent(payload.agent_id)
    return {"status": "success", "message": f"Agent {payload.agent_id} resumed"}


@app.post("/api/background/mcp/connect")
async def connect_background_mcp(payload: BackgroundAgentRequest, request: Request):
    await request.app.state.bg_service.connect_mcp(payload.agent_id)
    return {
        "status": "success",
        "message": f"MCP connected for agent {payload.agent_id}",
    }


@app.post("/api/task/update")
async def update_task(payload: TaskUpdateRequest, request: Request):
    ok = request.app.state.bg_service.update_task_config(
        payload.agent_id,
        {"query": payload.query},
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update task config")
    return {
        "status": "success",
        "message": f"Task updated for agent {payload.agent_id}",
    }


@app.delete("/api/task/remove/{agent_id}")
async def remove_task(agent_id: str, request: Request):
    ok = request.app.state.bg_service.remove_task(agent_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to remove task")
    return {"status": "success", "message": f"Task removed for agent {agent_id}"}


@app.get("/api/events")
async def get_events(request: Request):
    # Optional: ?session_id=... to fetch events for a session; returns [] if not provided
    session_id = request.query_params.get("session_id")
    if not session_id:
        return {"status": "success", "events": []}
    events = await request.app.state.main_service.event_router.get_events(
        session_id=session_id
    )

    # Best-effort serialization
    def to_jsonable(ev):
        try:
            return (
                ev
                if isinstance(ev, (str, int, float, bool, type(None), dict, list))
                else getattr(ev, "to_dict", lambda: str(ev))()
            )
        except Exception:
            return str(ev)

    return {"status": "success", "events": [to_jsonable(e) for e in events]}


@app.get("/api/events/stream/{session_id}")
async def stream_events(session_id: str, request: Request):
    async def event_generator():
        try:
            async for event in request.app.state.main_service.event_router.stream(
                session_id=session_id
            ):
                # Best-effort serialization
                try:
                    payload = (
                        event
                        if isinstance(event, (str, bytes))
                        else getattr(event, "to_dict", lambda: str(event))()
                    )
                except Exception:
                    payload = str(event)
                yield f"data: {json.dumps({'type': 'event', 'event': payload, 'session_id': session_id})}\n\n"
                await asyncio.sleep(0)
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'content': str(exc), 'session_id': session_id})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/plain")


@app.get("/api/background/status")
async def background_manager_status(request: Request):
    return {
        "status": "success",
        "manager": request.app.state.bg_service.get_manager_status(),
    }


@app.get("/api/background/status/{agent_id}")
async def background_agent_status(agent_id: str, request: Request):
    status = request.app.state.bg_service.get_agent_status(agent_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {"status": "success", "agent": status}


@app.get("/api/agent/info")
async def get_agent_info(request: Request):
    ms: MainAgentService = request.app.state.main_service
    bg: BackgroundAgentService = request.app.state.bg_service
    info = {
        "name": ms.agent.name if ms.agent else "unknown",
        "status": "active" if ms.agent else "inactive",
        "memory_store": type(ms.memory_router).__name__
        if ms.memory_router
        else "unknown",
        "event_store": type(ms.event_router).__name__ if ms.event_router else "unknown",
        "background_agents": len(bg.list()),
        "memory_backend": getattr(ms.memory_router, "memory_store_type", None),
        "event_backend": ms.event_router.get_event_store_type()
        if ms.event_router
        else None,
    }
    return {"status": "success", "info": info}


@app.get("/api/tools")
async def get_tools(request: Request):
    # check for both mcp tools and local tools

    return {
        "status": "success",
        "tools": await request.app.state.main_service.agent.list_all_available_tools(),
    }


@app.post("/api/switch-backend")
async def switch_backend(request: Request):
    data = await request.json()
    backend_type = data.get("type")  # "memory" | "event"
    backend = data.get("backend")
    if backend_type not in ("memory", "event") or not backend:
        raise HTTPException(status_code=400, detail="Invalid switch-backend payload")

    ms: MainAgentService = request.app.state.main_service
    bg: BackgroundAgentService = request.app.state.bg_service

    if backend_type == "memory":
        ms.switch_memory_backend(backend)
        # Propagate to background manager without recreating
        bg.manager.memory_router = ms.memory_router
        return {"status": "success", "message": f"Memory backend switched to {backend}"}

    if backend_type == "event":
        ms.switch_event_backend(backend)
        bg.manager.event_router = ms.event_router
        return {"status": "success", "message": f"Event backend switched to {backend}"}

    raise HTTPException(status_code=400, detail="Unsupported backend type")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    app.state.active_connections.append(websocket)
    try:
        while True:
            # Echo keepalive with timestamp
            await websocket.receive_text()
            await websocket.send_text(
                json.dumps({"type": "ping", "timestamp": datetime.now().isoformat()})
            )
    except WebSocketDisconnect:
        app.state.active_connections.remove(websocket)


@app.get("/health")
async def health_check(request: Request):
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "agent_initialized": request.app.state.main_service.agent is not None,
        "active_connections": len(request.app.state.active_connections),
    }


def main():
    print("🚀 Starting Enhanced OmniCoreAgent Web Server...")
    print(f"📁 Static files directory: {STATIC_DIR}")
    print(f"📁 Templates directory: {TEMPLATES_DIR}")
    print("📖 API Documentation: http://localhost:8001/docs")
    print("🔍 Interactive API: http://localhost:8001/redoc")
    print("🌐 Web Interface: http://localhost:8001")
    print("💡 Press Ctrl+C to stop")
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")


if __name__ == "__main__":
    main()
