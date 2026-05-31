from __future__ import annotations

import unittest

from content_builder.config import PROJECT_DIR, WORKSPACE_DIR, load_main_config, load_subagents_yaml
from content_builder.tools import TOOL_REGISTRY


class BackendConfigurationTests(unittest.TestCase):
    def test_active_capabilities_are_generic_and_skill_driven(self) -> None:
        config = load_main_config()

        self.assertEqual(config.name, "content-builder")
        self.assertEqual(config.conversation.max_turns, 50)
        self.assertEqual({path.rstrip("/").split("/")[-1] for path in config.skills}, {"skills"})
        self.assertEqual(set(config.tools), {"generate_image"})
        self.assertIn("generate_image", TOOL_REGISTRY)

        subagents = load_subagents_yaml(config.subagents_config)
        self.assertIn("researcher", subagents)
        self.assertIn("artifact_reviewer", subagents)

    def test_general_prompt_does_not_embed_storybook_domain_rules(self) -> None:
        prompt = (PROJECT_DIR / "AGENTS.md").read_text(encoding="utf-8")
        self.assertNotIn("绘本", prompt)
        self.assertNotIn("storybook", prompt.lower())

    def test_game_outputs_are_routed_to_ignored_workspace_games_dir(self) -> None:
        prompt = (PROJECT_DIR / "AGENTS.md").read_text(encoding="utf-8")
        root_gitignore = (WORKSPACE_DIR / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("/games/<game-slug>/", prompt)
        self.assertIn("python -m http.server <port> --bind 127.0.0.1", prompt)
        self.assertIn("/games/", root_gitignore.splitlines())

    def test_voice_config_and_local_secrets_are_declared(self) -> None:
        config = load_main_config()
        root_gitignore = (WORKSPACE_DIR / ".gitignore").read_text(encoding="utf-8")

        self.assertEqual(config.voice.asr.provider, "funasr")
        self.assertEqual(config.voice.tts.provider, "qwen")
        self.assertEqual(config.voice.tts.model, "qwen3-tts-flash")
        self.assertEqual(config.voice.asr.model_dir.name, "SenseVoiceSmall")
        self.assertTrue(config.secrets.path.name, "secrets.local.yaml")
        self.assertIn("/wangpu/content-builder-agent/secrets.local.yaml", root_gitignore.splitlines())


if __name__ == "__main__":
    unittest.main()
