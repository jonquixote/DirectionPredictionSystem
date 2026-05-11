"""Composable filter pipeline.

Each layer's stage function receives a mutable context dict and
returns a FilterDecision. The pipeline runs stages in order. The
first stage that returns ``passed=False`` blocks the trade and the
rest are skipped. All evaluations (passed and blocked) are collected
into ``result.evals`` for the verbose decision trace.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass(frozen=True)
class FilterEval:
    name: str
    threshold: Optional[float]
    input_value: Optional[float]
    passed: bool


@dataclass(frozen=True)
class FilterDecision:
    passed: bool
    name: str = ""
    reason: Optional[str] = None
    threshold: Optional[float] = None
    input_value: Optional[float] = None

    @classmethod
    def pass_(cls, threshold=None, input_value=None) -> "FilterDecision":
        return cls(passed=True, threshold=threshold, input_value=input_value)

    @classmethod
    def block(cls, reason: str, threshold=None, input_value=None) -> "FilterDecision":
        return cls(passed=False, reason=reason,
                   threshold=threshold, input_value=input_value)

    @classmethod
    def eval_(cls, *, threshold, input_value, passed,
              reason=None) -> "FilterDecision":
        return cls(passed=passed, threshold=threshold,
                   input_value=input_value, reason=reason)


@dataclass(frozen=True)
class FilterStage:
    name: str
    fn: Callable


@dataclass
class PipelineResult:
    passed: bool
    blocked_at: Optional[str]
    reason: Optional[str]
    evals: List[FilterEval] = field(default_factory=list)


class FilterPipeline:
    def __init__(self, stages: List[FilterStage]) -> None:
        self._stages = list(stages)

    def run(self, ctx: dict) -> PipelineResult:
        evals: List[FilterEval] = []
        for stage in self._stages:
            decision = stage.fn(ctx)
            evals.append(FilterEval(
                name=stage.name,
                threshold=decision.threshold,
                input_value=decision.input_value,
                passed=decision.passed,
            ))
            if not decision.passed:
                return PipelineResult(
                    passed=False, blocked_at=stage.name,
                    reason=decision.reason or stage.name, evals=evals,
                )
        return PipelineResult(
            passed=True, blocked_at=None, reason=None, evals=evals,
        )
