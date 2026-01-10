from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from redis import asyncio as aioredis


class ResultStore(ABC):
    @abstractmethod
    async def save_result(self, task_id: str, result: Dict[str, Any]) -> None:
        pass

    @abstractmethod
    async def get_result(self, task_id: str) -> Optional[Dict[str, Any]]:
        pass


class RedisResultStore(ResultStore):
    def __init__(self, redi_url: str):
        self.redi_url = redi_url
        self._redis_client = None

    async def connect(self):
        self._redis_client = await aioredis.from_url(
            self.redi_url, decode_responses=True
        )

    async def save_result(self, task_id: str, result: Dict[str, Any]) -> None:
        # connect if not already connected
        if not self._redis_client:
            await self.connect()
        import json

        key = f"omni:result:{task_id}"
        await self._redis_client.setex(key, 86400, json.dumps(result, default=str))

    async def get_result(self, task_id: str) -> Optional[Dict[str, Any]]:
        # connect if not already connected
        if not self._redis_client:
            await self.connect()
        key = f"omni:result:{task_id}"
        data = await self._redis_client.get(key)
        if data:
            import json

            return json.loads(data)
        return None
