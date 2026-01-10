from fastapi import FastAPI, HTTPException
import uvicorn
from typing import Dict, Any

from omnidaemon.sdk import OmniDaemonSDK


def create_app(sdk: OmniDaemonSDK) -> FastAPI:
    app = FastAPI(
        title="OmniDaemon Control API",
        description="HTTP API to manage agents, tasks, and health of a running OmniDaemon instance.",
        version="0.1.0",
    )

    # -------------------------
    # Agent Management
    # -------------------------
    @app.get("/agents")
    async def list_agents():
        """List all registered agents grouped by topic."""
        return sdk.list_agents()

    @app.get("/agents/{topic}/{name}")
    async def get_agent(topic: str, name: str):
        """Get a specific agent by topic and name."""
        agent = sdk.get_agent(topic, name)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return agent

    @app.post("/agents/{topic}/{name}/start")
    async def start_agent(topic: str, name: str):
        """Start a stopped agent."""
        try:
            await sdk.start_agent(topic, name)
            return {"status": "started", "agent": name, "topic": topic}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/agents/{topic}/{name}/stop")
    async def stop_agent(topic: str, name: str):
        """Stop a running agent."""
        try:
            await sdk.stop_agent(topic, name)
            return {"status": "stopped", "agent": name, "topic": topic}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/agents/start")
    async def start_all_agents():
        """Start all agents."""
        await sdk.start_all_agents()
        return {"status": "all agents started"}

    @app.post("/agents/stop")
    async def stop_all_agents():
        """Stop all agents."""
        await sdk.stop_all_agents()
        return {"status": "all agents stopped"}

    # -------------------------
    # Health & Metrics
    # -------------------------
    @app.get("/health")
    async def health():
        """Get runner health and status."""
        return sdk.health()

    # -------------------------
    # Task Management
    # -------------------------
    @app.post("/tasks")
    async def publish_task(payload: Dict[str, Any]):
        """
        Publish a new task.
        Expected body: {"topic": "recipe.tasks", "payload": {...}, "reply_to": "optional_webhook"}
        """
        topic = payload.get("topic")
        task_payload = payload.get("payload", {})
        reply_to = payload.get("reply_to")

        if not topic:
            raise HTTPException(
                status_code=400, detail="Missing 'topic' in request body"
            )

        try:
            task_id = await sdk.publish_task(topic, task_payload, reply_to)
            return {"task_id": task_id, "status": "published"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to publish task: {e}")

    @app.get("/tasks/{task_id}")
    async def get_task_result(task_id: str):
        """Get task result by ID."""
        result = await sdk.get_result(task_id)
        if result is None:
            raise HTTPException(
                status_code=404, detail="Task not found or not completed"
            )
        return result

    @app.get("/metrics")
    async def metrics():
        return sdk.metrics()

    return app


async def start_api_server(
    sdk: OmniDaemonSDK, host: str = "127.0.0.1", port: int = 8000
):
    """
    Start the FastAPI server in the background.
    Call this from sdk.run() or manually.
    """
    app = create_app(sdk)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()
