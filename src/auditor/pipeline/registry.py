"""Pluggable pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from auditor.contracts.enums import JobStage, StageRunStatus
from auditor.pipeline.context import JobContext
from auditor.pipeline.events import EventBus


@dataclass(frozen=True, slots=True)
class StageResult:
    """Outcome of a single stage invocation."""

    status: StageRunStatus
    message: str | None = None
    hard_fail: bool = False
    """If true, abort the remaining pipeline (e.g. compile failure)."""
    artifact_paths: tuple[str, ...] = ()
    skip_reason: str | None = None


class Stage(Protocol):
    """One pipeline stage."""

    name: str
    job_stage: JobStage
    optional: bool

    def should_run(self, ctx: JobContext) -> tuple[bool, str | None]:
        """Return (run?, skip_reason)."""
        ...

    def run(self, ctx: JobContext, bus: EventBus) -> StageResult:
        """Execute the stage. Must not raise for soft failures when optional."""
        ...


class StageRegistry:
    """Ordered registry of stages by name."""

    def __init__(self) -> None:
        self._stages: dict[str, Stage] = {}
        self._order: list[str] = []

    def register(self, stage: Stage, *, order_hint: int | None = None) -> None:
        self._stages[stage.name] = stage
        if stage.name not in self._order:
            if order_hint is None:
                self._order.append(stage.name)
            else:
                self._order.insert(min(order_hint, len(self._order)), stage.name)

    def get(self, name: str) -> Stage | None:
        return self._stages.get(name)

    def resolve(self, names: list[str]) -> list[Stage]:
        out: list[Stage] = []
        for name in names:
            stage = self._stages.get(name)
            if stage is not None:
                out.append(stage)
        return out

    def all_names(self) -> list[str]:
        return list(self._order)
