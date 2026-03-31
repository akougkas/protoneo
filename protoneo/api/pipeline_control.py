"""Per-session pipeline control for human-in-the-loop gating."""

import asyncio


class PipelineControl:
    """Per-session pipeline control for human-in-the-loop gating.

    The pipeline has stages with optional gates where the user can
    inspect intermediate results before proceeding.
    """

    STAGES = ["pre_review", "review", "post_review"]

    PRE_REVIEW_STEPS = ["parse", "metadata", "ontology", "extract", "coref", "verify", "summarize"]
    REVIEW_STEPS = ["independent_reviews", "deliberation", "meta_review", "pc_chair"]

    def __init__(self):
        self.auto_advance: bool = True
        self.skip_gate: bool = False
        self.current_stage: str = ""
        self.current_step: str = ""
        self.completed_stages: list[str] = []
        self._gate: asyncio.Event = asyncio.Event()
        self._gate.set()
        self.paused: bool = False
        self.cancelled: bool = False
        self._task: asyncio.Task | None = None

    def set_task(self, task: asyncio.Task) -> None:
        self._task = task

    def enter_stage(self, stage: str) -> None:
        self.current_stage = stage
        self.current_step = ""

    def enter_step(self, step: str) -> None:
        self.current_step = step

    def stage_done(self, stage: str) -> None:
        if stage not in self.completed_stages:
            self.completed_stages.append(stage)
        self.current_step = ""

    async def wait_for_gate(self) -> None:
        """Block at the mandatory pre_review -> review gate.

        When skip_gate is True, the gate is skipped entirely so the
        pipeline runs straight through without human interaction.
        """
        if self.skip_gate:
            return
        self._gate.clear()
        self.paused = True
        await self._gate.wait()
        self.paused = False
        if self.cancelled:
            raise asyncio.CancelledError("Pipeline cancelled by user")

    async def wait_if_paused(self) -> None:
        """Block only if manually paused (not for mandatory gates)."""
        if not self.auto_advance:
            self._gate.clear()
            self.paused = True
            await self._gate.wait()
            self.paused = False
        if self.cancelled:
            raise asyncio.CancelledError("Pipeline cancelled by user")

    def advance(self) -> None:
        self.paused = False
        self._gate.set()

    def pause(self) -> None:
        self.auto_advance = False
        self.paused = True
        self._gate.clear()

    def resume(self) -> None:
        self.auto_advance = True
        self.paused = False
        self._gate.set()

    def cancel(self) -> None:
        self.cancelled = True
        self.paused = False
        self._gate.set()
        if self._task and not self._task.done():
            self._task.cancel()

    def status(self) -> dict:
        return {
            "current_stage": self.current_stage,
            "current_step": self.current_step,
            "completed_stages": self.completed_stages,
            "auto_advance": self.auto_advance,
            "skip_gate": self.skip_gate,
            "paused": self.paused,
            "cancelled": self.cancelled,
        }
