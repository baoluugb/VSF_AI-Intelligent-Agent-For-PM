"""Legacy placeholder — superseded by Week 3 implementation.

The active agent implementation is at:
  src/agents/report_agent.py  →  ReportAgent / run_report_agent()

This file is kept only for backwards-compatibility; do NOT add new logic here.
"""
from __future__ import annotations

import warnings


class CoreAgent:
    """Deprecated skeleton agent.

    .. deprecated::
        Use :class:`agents.report_agent.ReportAgent` instead.
    """

    def __init__(self) -> None:
        warnings.warn(
            "CoreAgent is a deprecated placeholder. "
            "Use agents.report_agent.ReportAgent instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.name = "Core Agent"
        self.version = "1.0.0"

    def start(self) -> None:
        print(f"{self.name} v{self.version} is starting...")

    def stop(self) -> None:
        print(f"{self.name} is stopping...")

    def process_data(self, data):
        # Placeholder — not implemented
        print("Processing data...")
        return data

    def report_status(self) -> None:
        print(f"{self.name} is running smoothly.")