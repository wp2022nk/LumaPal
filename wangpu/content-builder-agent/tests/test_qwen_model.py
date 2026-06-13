from __future__ import annotations

import unittest
from unittest.mock import patch

from content_builder.config import ModelConfig, create_qwen_model


class QwenModelFactoryTests(unittest.TestCase):
    def test_create_qwen_model_uses_official_init_chat_model(self) -> None:
        sentinel = object()
        config = ModelConfig(
            model="qwen-test",
            api_key="test-key",
            base_url="https://example.test/v1",
            enable_thinking=True,
            thinking_budget=1024,
        )

        with patch("langchain.chat_models.init_chat_model", return_value=sentinel) as init_chat_model:
            model = create_qwen_model(config)

        self.assertIs(model, sentinel)
        init_chat_model.assert_called_once_with(
            model="qwen-test",
            model_provider="openai",
            base_url="https://example.test/v1",
            api_key="test-key",
            streaming=True,
            use_responses_api=False,
        )

    def test_create_qwen_model_requires_api_key(self) -> None:
        config = ModelConfig(model="qwen-test", api_key=None, base_url="https://example.test/v1")

        with self.assertRaises(RuntimeError):
            create_qwen_model(config)


if __name__ == "__main__":
    unittest.main()
