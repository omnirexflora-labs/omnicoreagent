# deep_coder/api.py
from fastapi import FastAPI, HTTPException
from agent_runner import DeepCodingAgentRunner
from sandbox.utils import create_tarball
import asyncio

app = FastAPI()
sessions = {}


@app.post("/chat")
async def chat(query: str):
    runner = DeepCodingAgentRunner()
    sessions[runner.session_id] = runner
    result = await runner.handle_chat(query)
    return result


@app.get("/download/{session_id}.tar.gz")
async def download(session_id: str):
    if session_id not in sessions:
        raise HTTPException(404)
    tar_path = create_tarball(session_id, "./user_workspaces", "./outputs")
    return FileResponse(
        tar_path, media_type="application/gzip", filename=f"{session_id}.tar.gz"
    )
