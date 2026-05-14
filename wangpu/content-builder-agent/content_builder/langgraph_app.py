"""LangGraph Agent Server entrypoint for the Vue demo console."""

from __future__ import annotations

from content_builder.agent_factory import create_content_writer


agent = create_content_writer(runtime_mode="web")
