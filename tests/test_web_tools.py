from __future__ import annotations

import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest

from argus.ai.provider import ToolCall
from argus.config import ApplicationConfig, ToolsConfig, WebApplicationConfig
from argus.tools.computer import build_computer_tool_runtime
from argus.tools.web import validate_public_url


class WebToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = ToolsConfig(
            enabled=True,
            allowed_roots=(Path(self.temporary.name),),
            applications=(ApplicationConfig("notepad", "notepad.exe"),),
            web_applications=(
                WebApplicationConfig("youtube", "https://www.youtube.com/"),
            ),
            allowed_commands=("whoami.exe",),
        )

    @staticmethod
    def public_dns(*args: object, **kwargs: object) -> list[tuple[object, ...]]:
        del args, kwargs
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    def test_opens_named_allowlisted_web_application(self) -> None:
        runtime = build_computer_tool_runtime(self.config)
        with (
            patch("argus.tools.web.socket.getaddrinfo", side_effect=self.public_dns),
            patch("argus.tools.web.webbrowser.open", return_value=True) as open_browser,
        ):
            result = json.loads(
                runtime.execute(
                    ToolCall(
                        "1",
                        "open_web_application",
                        '{"application":"youtube"}',
                    )
                )
            )

        self.assertTrue(result["ok"])
        open_browser.assert_called_once_with("https://www.youtube.com/", new=2)

    def test_blocks_private_and_local_network_urls(self) -> None:
        for url in (
            "http://127.0.0.1/",
            "http://10.0.0.1/",
            "http://localhost/",
            "http://[::1]/",
        ):
            with self.subTest(url=url), self.assertRaisesRegex(
                ValueError, "private|Localhost"
            ):
                validate_public_url(url)

    def test_blocks_domain_that_resolves_to_private_address(self) -> None:
        with self.assertRaisesRegex(ValueError, "resolves to a local"):
            validate_public_url(
                "https://example.test/",
                resolver=lambda *args, **kwargs: [
                    (2, 1, 6, "", ("192.168.1.9", 443))
                ],
            )

    def test_search_returns_bounded_current_results(self) -> None:
        document = """
        <html><body>
          <div class="result">
            <a class="result__a" href="https://example.com/news">Robotics News</a>
            <a class="result__snippet">A current robotics update.</a>
          </div>
          <div class="result">
            <a class="result__a" href="https://example.org/second">Second Result</a>
            <div class="result__snippet">More details.</div>
          </div>
        </body></html>
        """
        runtime = build_computer_tool_runtime(self.config)
        with patch(
            "argus.tools.web._download_text",
            return_value=(document, "https://html.duckduckgo.com/html/", "text/html"),
        ):
            result = json.loads(
                runtime.execute(
                    ToolCall(
                        "1",
                        "search_web",
                        '{"query":"robotics news","max_results":1}',
                    )
                )
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "DuckDuckGo")
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["results"][0]["title"], "Robotics News")
        self.assertIn("untrusted", result["notice"].casefold())

    def test_page_reader_removes_scripts_and_limits_output(self) -> None:
        document = """
        <html><head><title>Example</title><script>ignore me</script></head>
        <body><h1>Hello</h1><p>Useful page text.</p></body></html>
        """
        runtime = build_computer_tool_runtime(self.config)
        with patch(
            "argus.tools.web._download_text",
            return_value=(document, "https://example.com/", "text/html"),
        ):
            result = json.loads(
                runtime.execute(
                    ToolCall(
                        "1",
                        "read_web_page",
                        '{"url":"https://example.com/","max_characters":1000}',
                    )
                )
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["title"], "Example")
        self.assertIn("Useful page text", result["text"])
        self.assertNotIn("ignore me", result["text"])


if __name__ == "__main__":
    unittest.main()
