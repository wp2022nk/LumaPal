from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from typing import Any

from content_builder.streaming import ConsoleStreamPrinter, StreamEvent, stream_agent_events


class ConsoleStreamPrinterTests(unittest.TestCase):
    def render(self, events: list[StreamEvent], **printer_options: Any) -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            printer = ConsoleStreamPrinter(use_color=False, **printer_options)
            for event in events:
                printer.print(event)
            printer.finish()
        return buffer.getvalue()

    def test_thinking_is_shown_by_default_and_answer_has_its_own_label(self) -> None:
        output = self.render(
            [
                StreamEvent("thinking", "main", "The user is greeting me."),
                StreamEvent("token", "main", "你好！"),
                StreamEvent("final", "main", "你好！"),
            ]
        )

        self.assertIn("思考> The user is greeting me.", output)
        self.assertIn("\n助手> 你好！", output)

    def test_thinking_can_be_hidden_without_mixing_into_answer(self) -> None:
        output = self.render(
            [
                StreamEvent("thinking", "main", "先判断用户意图。"),
                StreamEvent("token", "main", "可以。"),
                StreamEvent("final", "main", "可以。"),
            ],
            show_thinking=False,
        )

        self.assertNotIn("思考>", output)
        self.assertIn("\n助手> 可以。", output)

    def test_debug_graph_events_are_shown_by_default(self) -> None:
        output = self.render(
            [
                StreamEvent("task", "main", "[task:start] main -> model (abc123)"),
                StreamEvent("node_update", "main", "[main] 节点更新: model"),
                StreamEvent("token", "main", "完成"),
                StreamEvent("final", "main", "完成"),
            ]
        )

        self.assertIn("task:start", output)
        self.assertIn("节点更新", output)
        self.assertIn("\n助手> 完成", output)

    def test_final_text_is_printed_when_no_tokens_were_streamed(self) -> None:
        output = self.render([StreamEvent("final", "main", "完整回答")])

        self.assertEqual(output, "\n助手> 完整回答\n")

    def test_tool_events_are_rendered_as_separate_panels(self) -> None:
        output = self.render(
            [
                StreamEvent("tool_call", "main", "[main] 调用工具: generate_image\n{}"),
                StreamEvent("token", "main", "已生成。"),
                StreamEvent("final", "main", "已生成。"),
            ]
        )

        self.assertIn("> 工具调用", output)
        self.assertIn("调用工具: generate_image", output)
        self.assertIn("\n助手> 已生成。", output)


class StreamAgentEventsTests(unittest.TestCase):
    def test_v3_message_reasoning_tool_and_custom_events_are_normalized(self) -> None:
        class FakeAgent:
            def stream_events(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
                return [
                    {
                        "method": "messages",
                        "params": {
                            "namespace": [],
                            "data": [
                                {
                                    "event": "content-block-delta",
                                    "delta": {"type": "reasoning-delta", "reasoning": "思考中"},
                                }
                            ],
                        },
                    },
                    {
                        "method": "tools",
                        "params": {
                            "namespace": [],
                            "data": {
                                "event": "tool-started",
                                "tool_name": "generate_image",
                                "input": {"prompt": "moon"},
                            },
                        },
                    },
                    {
                        "method": "custom",
                        "params": {
                            "namespace": [],
                            "data": {
                                "type": "sandbox_output",
                                "event": "chunk",
                                "command": "python script.py",
                                "chunk": "line 1\n",
                            },
                        },
                    },
                    {
                        "method": "messages",
                        "params": {
                            "namespace": [],
                            "data": [
                                {
                                    "event": "content-block-delta",
                                    "delta": {"type": "text-delta", "text": "完成"},
                                }
                            ],
                        },
                    },
                ]

        events = list(stream_agent_events(FakeAgent(), "测试", thread_id="test-thread"))

        self.assertEqual([event.type for event in events], ["thinking", "tool_call", "sandbox_output", "token", "final"])
        self.assertEqual(events[0].text, "思考中")
        self.assertIn("generate_image", events[1].text)
        self.assertEqual(events[2].text, "line 1\n")
        self.assertEqual(events[-1].text, "完成")

    def test_v3_tuple_message_payloads_stream_tokens(self) -> None:
        class FakeAgent:
            def stream_events(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
                return [
                    {
                        "method": "messages",
                        "params": {
                            "namespace": [],
                            "data": (
                                {"event": "message-start", "role": "ai", "id": "run-1"},
                                {"langgraph_node": "model"},
                            ),
                        },
                    },
                    {
                        "method": "messages",
                        "params": {
                            "namespace": [],
                            "data": (
                                {
                                    "event": "content-block-delta",
                                    "delta": {"type": "text-delta", "text": "流式"},
                                },
                                {"langgraph_node": "model"},
                            ),
                        },
                    },
                ]

        events = list(stream_agent_events(FakeAgent(), "测试", thread_id="test-thread"))

        self.assertEqual([event.type for event in events], ["node_update", "token", "final"])
        self.assertIn("model", events[0].text)
        self.assertEqual(events[1].text, "流式")
        self.assertEqual(events[-1].text, "流式")

    def test_v3_subgraph_tokens_do_not_leak_into_final_answer(self) -> None:
        class FakeAgent:
            def stream_events(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
                return [
                    {
                        "method": "messages",
                        "params": {
                            "namespace": ["tools:subagent123"],
                            "data": [
                                {
                                    "event": "content-block-delta",
                                    "delta": {"type": "text-delta", "text": "子智能体中间输出"},
                                }
                            ],
                        },
                    },
                    {
                        "method": "messages",
                        "params": {
                            "namespace": [],
                            "data": [
                                {
                                    "event": "content-block-delta",
                                    "delta": {"type": "text-delta", "text": "最终回答"},
                                }
                            ],
                        },
                    },
                ]

        events = list(stream_agent_events(FakeAgent(), "测试", thread_id="test-thread"))

        self.assertEqual(events[-1].type, "final")
        self.assertEqual(events[-1].text, "最终回答")

    def test_v3_stream_events_is_required(self) -> None:
        class FakeAgent:
            pass

        with self.assertRaisesRegex(RuntimeError, 'stream_events\\(version="v3"\\)'):
            list(stream_agent_events(FakeAgent(), "测试", thread_id="test-thread"))


if __name__ == "__main__":
    unittest.main()
