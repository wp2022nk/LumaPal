from __future__ import annotations

import unittest

from content_builder.server.__main__ import _ignore_expected_windows_disconnect


class WindowsDisconnectHandlerTests(unittest.TestCase):
    def test_expected_connection_reset_is_ignored(self) -> None:
        class FakeLoop:
            def __init__(self) -> None:
                self.contexts: list[dict[str, object]] = []

            def default_exception_handler(self, context: dict[str, object]) -> None:
                self.contexts.append(context)

        loop = FakeLoop()
        _ignore_expected_windows_disconnect(loop, {"exception": ConnectionResetError()})  # type: ignore[arg-type]

        self.assertEqual(loop.contexts, [])

    def test_unexpected_error_still_reaches_default_handler(self) -> None:
        class FakeLoop:
            def __init__(self) -> None:
                self.contexts: list[dict[str, object]] = []

            def default_exception_handler(self, context: dict[str, object]) -> None:
                self.contexts.append(context)

        loop = FakeLoop()
        context = {"exception": RuntimeError("unexpected")}
        _ignore_expected_windows_disconnect(loop, context)  # type: ignore[arg-type]

        self.assertEqual(loop.contexts, [context])


if __name__ == "__main__":
    unittest.main()
