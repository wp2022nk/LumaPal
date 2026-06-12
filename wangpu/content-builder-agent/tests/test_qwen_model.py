from __future__ import annotations

import unittest

from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatResult

from content_builder.qwen_model import _ensure_tool_call_ids_on_message, _ensure_tool_call_ids_on_result


class QwenModelAdapterTests(unittest.TestCase):
    def test_missing_tool_call_ids_are_filled(self) -> None:
        result = ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "",
                                "name": "task",
                                "args": {
                                    "subagent_type": "researcher",
                                    "description": "Research current headlines.",
                                },
                                "type": "tool_call",
                            }
                        ],
                    )
                )
            ]
        )

        _ensure_tool_call_ids_on_result(result)

        tool_call = result.generations[0].message.tool_calls[0]
        self.assertTrue(tool_call["id"].startswith("call_qwen_"))

    def test_existing_tool_call_ids_are_preserved(self) -> None:
        result = ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "call_existing",
                                "name": "generate_image",
                                "args": {"prompt": "moon"},
                                "type": "tool_call",
                            }
                        ],
                    )
                )
            ]
        )

        _ensure_tool_call_ids_on_result(result)

        tool_call = result.generations[0].message.tool_calls[0]
        self.assertEqual(tool_call["id"], "call_existing")

    def test_streaming_tool_call_chunks_get_stable_ids(self) -> None:
        chunk = AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "id": "",
                    "name": "task",
                    "args": '{"subagent_type":"researcher"}',
                    "index": 0,
                    "type": "tool_call_chunk",
                }
            ],
        )

        _ensure_tool_call_ids_on_message(chunk, "call_qwen_stream")

        self.assertEqual(chunk.tool_call_chunks[0]["id"], "call_qwen_stream_0")


if __name__ == "__main__":
    unittest.main()
