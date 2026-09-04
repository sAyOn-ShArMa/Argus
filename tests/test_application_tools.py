from __future__ import annotations

from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest

from argus.config import ApplicationConfig, ToolsConfig
from argus.tools.applications import (
    InstalledApplication,
    InstalledApplicationCatalog,
    discover_installed_applications,
)
from argus.tools.computer import build_computer_tool_definitions


class InstalledApplicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = ToolsConfig(
            enabled=True,
            allowed_roots=(Path.cwd(),),
            applications=(ApplicationConfig("notepad", "notepad.exe"),),
            allowed_commands=("whoami.exe",),
        )

    def test_exact_discovered_name_launches_only_discovered_target(self) -> None:
        applications = (
            InstalledApplication("Arduino IDE", "start_menu", r"C:\Menu\Arduino.lnk"),
            InstalledApplication("Arduino Cloud", "start_menu", r"C:\Menu\Cloud.lnk"),
        )
        launched: list[InstalledApplication] = []
        catalog = InstalledApplicationCatalog(
            self.config,
            discoverer=lambda config: applications,
            launcher=launched.append,
        )

        result = catalog.open("Arduino IDE")

        self.assertTrue(result["started"])
        self.assertEqual(result["application"], "Arduino IDE")
        self.assertEqual(launched, [applications[0]])

    def test_ambiguous_name_does_not_launch_anything(self) -> None:
        applications = (
            InstalledApplication("Visual Studio", "start_menu", "one.lnk"),
            InstalledApplication("Visual Studio Code", "start_menu", "two.lnk"),
        )
        launched: list[InstalledApplication] = []
        catalog = InstalledApplicationCatalog(
            self.config,
            discoverer=lambda config: applications,
            launcher=launched.append,
        )

        with self.assertRaisesRegex(LookupError, "ambiguous"):
            catalog.open("Visual")

        self.assertEqual(launched, [])

    def test_start_menu_shortcuts_are_discovered_on_demand(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            common = root / "common"
            shortcut_folder = (
                common / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            )
            shortcut_folder.mkdir(parents=True)
            shortcut = shortcut_folder / "Example App.lnk"
            shortcut.write_bytes(b"test shortcut placeholder")
            with (
                patch.dict(
                    "os.environ",
                    {"ProgramData": str(common), "APPDATA": str(root / "missing")},
                ),
                patch("argus.tools.applications.platform.system", return_value="Windows"),
                patch("argus.tools.applications._discover_app_ids", return_value=()),
                patch(
                    "argus.tools.applications._discover_registry_applications",
                    return_value=(),
                ),
            ):
                applications = discover_installed_applications(self.config)

        names = {item.name for item in applications}
        self.assertIn("Example App", names)
        self.assertIn("notepad", names)

    def test_computer_runtime_includes_dynamic_application_tools(self) -> None:
        names = {
            definition.name for definition in build_computer_tool_definitions(self.config)
        }

        self.assertIn("find_installed_applications", names)
        self.assertIn("open_installed_application", names)


if __name__ == "__main__":
    unittest.main()
