"""LangGraph Agent Server entrypoint for the content builder backend."""

from __future__ import annotations

from content_builder.agent_factory import create_content_writer


def make_agent(_config=None):
    """Build the graph lazily so the settings page works before API keys exist."""

    return create_content_writer(runtime_mode="server")
