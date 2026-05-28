from __future__ import annotations

import unittest

from content_builder.config import PROJECT_DIR
from content_builder.manifest import build_manifest


class ManifestConfigurationTests(unittest.TestCase):
    def test_active_capabilities_are_generic_and_skill_driven(self) -> None:
        manifest = build_manifest()
        self.assertEqual(manifest["agent"]["name"], "content-builder")
        self.assertEqual(manifest["agent"]["conversation"]["max_turns"], 50)
        self.assertEqual(
            {skill["name"] for skill in manifest["skills"]},
            {"imagegen", "storybook"},
        )
        configured_tools = {
            tool["name"]
            for tool in manifest["tools"]
            if tool.get("configured")
        }
        self.assertEqual(configured_tools, {"generate_image"})

    def test_general_prompt_does_not_embed_storybook_domain_rules(self) -> None:
        prompt = (PROJECT_DIR / "AGENTS.md").read_text(encoding="utf-8")
        self.assertNotIn("绘本", prompt)
        self.assertNotIn("storybook", prompt.lower())


if __name__ == "__main__":
    unittest.main()
