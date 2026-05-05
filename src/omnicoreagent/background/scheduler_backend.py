"""
APScheduler backend for background task scheduling.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from typing import Any, Callable, Dict, Union, Optional
from omnicoreagent.core.utils import logger
from .base import BackgroundTaskScheduler


class APSchedulerBackend(BackgroundTaskScheduler):
    """APScheduler-based background task scheduler."""

    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self._running = False

    def schedule_task(
        self, agent_id: str, interval: Union[int, str], task_fn: Callable, **kwargs
    ):
        """Schedule a task to run at specified intervals.

        Args:
            agent_id: Unique identifier for the agent
            interval: Interval in seconds (int) or cron expression (str)
            task_fn: Function to execute
            **kwargs: Additional arguments for the task function
        """
        if not asyncio.iscoroutinefunction(task_fn):
            raise ValueError("task_fn must be an async function for AsyncIOScheduler")
        try:
            if isinstance(interval, int):
                trigger = IntervalTrigger(seconds=interval)
            elif isinstance(interval, str):
                trigger = CronTrigger.from_crontab(interval)
            else:
                raise ValueError(f"Invalid interval type: {type(interval)}")

            self.scheduler.add_job(
                func=task_fn,
                trigger=trigger,
                id=agent_id,
                replace_existing=True,
                kwargs=kwargs,
                max_instances=1,
                coalesce=True,
            )
            logger.info(
                f"Scheduled task for agent {agent_id} with interval: {interval}"
            )
        except Exception as e:
            logger.error(f"Failed to schedule task for agent {agent_id}: {e}")
            raise

    def remove_task(self, agent_id: str):
        """Remove a scheduled task."""
        try:
            if self.scheduler.get_job(agent_id):
                self.scheduler.remove_job(agent_id)
                logger.info(f"Removed scheduled task for agent: {agent_id}")
            else:
                logger.warning(f"No scheduled task found for agent: {agent_id}")
        except Exception as e:
            logger.error(f"Failed to remove task for agent {agent_id}: {e}")
            raise

    def start(self):
        """Start the scheduler."""
        if not self._running:
            try:
                try:
                    self.scheduler.start()
                except RuntimeError:
                    logger.info(
                        "Re-initializing APScheduler as it was likely shut down"
                    )
                    self.scheduler = AsyncIOScheduler()
                    self.scheduler.start()

                self._running = True
                logger.info("APScheduler backend started")
            except Exception as e:
                logger.error(f"Failed to start APScheduler: {e}")
                raise

    def shutdown(self, wait: bool = True):
        """Shutdown the scheduler."""
        if self._running:
            try:
                self.scheduler.shutdown(wait=wait)
                self._running = False
                logger.info("APScheduler backend shutdown")
            except Exception as e:
                logger.error(f"Error during APScheduler shutdown: {e}")
                self._running = False

    def is_running(self) -> bool:
        """Check if the scheduler is running."""
        return self._running

    def is_task_scheduled(self, agent_id: str) -> bool:
        """Check if a task is scheduled for the given agent ID."""
        try:
            job = self.scheduler.get_job(agent_id)
            return job is not None
        except Exception as e:
            logger.error(
                f"Failed to check if task is scheduled for agent {agent_id}: {e}"
            )
            return False

    def get_next_run_time(self, agent_id: str) -> Optional[str]:
        """Get the next run time for a scheduled task."""
        try:
            job = self.scheduler.get_job(agent_id)
            if job and job.next_run_time:
                return job.next_run_time.isoformat()
            return None
        except Exception as e:
            logger.error(f"Failed to get next run time for agent {agent_id}: {e}")
            return None

    def get_job_status(self, agent_id: str) -> Dict[str, Any]:
        """Get the status of a scheduled job."""
        job = self.scheduler.get_job(agent_id)
        if job:
            return {
                "id": job.id,
                "next_run_time": getattr(job, "next_run_time", None),
                "trigger": str(job.trigger),
            }
        return {}

    def pause_job(self, agent_id: str):
        """Pause a scheduled job."""
        try:
            self.scheduler.pause_job(agent_id)
            logger.info(f"Paused job for agent: {agent_id}")
        except Exception as e:
            logger.error(f"Failed to pause job for agent {agent_id}: {e}")
            raise

    def resume_job(self, agent_id: str):
        """Resume a paused job."""
        try:
            self.scheduler.resume_job(agent_id)
            logger.info(f"Resumed job for agent: {agent_id}")
        except Exception as e:
            logger.error(f"Failed to resume job for agent {agent_id}: {e}")
            raise
