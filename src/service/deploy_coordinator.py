"""Small non-Qt coordinator for coalesced background Rime deployments."""
from __future__ import annotations

from dataclasses import dataclass
import queue
import threading

from src.service.deploy_service import DeployService


@dataclass(frozen=True)
class DeployOutcome:
    ok: bool
    message: str
    rerun_requested: bool = False


class AsyncDeployCoordinator:
    """Run at most one deploy and collapse repeated requests into one rerun."""

    def __init__(self, deploy_service: DeployService) -> None:
        self.deploy_service = deploy_service
        self.running = False
        self.pending = False
        self._results: queue.SimpleQueue[tuple[bool, str]] = queue.SimpleQueue()

    def request(self) -> bool:
        """Start a deploy, or remember one rerun; return whether a worker started."""
        if self.running:
            self.pending = True
            return False
        self.running = True

        def run() -> None:
            try:
                result = self.deploy_service.deploy()
            except Exception as exc:
                result = (False, f"部署失败：{exc}")
            self._results.put(result)

        threading.Thread(
            target=run, name="rime-config-deploy", daemon=True,
        ).start()
        return True

    def take_result(self) -> DeployOutcome | None:
        try:
            ok, message = self._results.get_nowait()
        except queue.Empty:
            return None
        rerun = self.pending
        self.running = False
        self.pending = False
        return DeployOutcome(bool(ok), str(message), rerun)
