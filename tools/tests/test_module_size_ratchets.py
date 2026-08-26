from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

MODULE_LINE_CAPS = {
    "apps/api/app/main.py": 22_248,
    "tools/check_platform_features.py": 9_313,
    "tools/platform_checker_source.py": 62,
    "tools/platform_contact_durability.py": 398,
    "tools/platform_contract_inventory.py": 995,
    "tools/platform_human_mode.py": 213,
    "tools/platform_public_profile.py": 90,
    "tools/platform_workspace_navigation.py": 60,
    "tools/platform_route_ownership.py": 247,
    "tools/platform_route_test_ownership.py": 1_787,
    "tools/platform_route_test_ui.py": 590,
}


class ModuleSizeRatchetTests(unittest.TestCase):
    def test_composition_roots_do_not_regrow(self) -> None:
        for relative_path, line_cap in MODULE_LINE_CAPS.items():
            with self.subTest(path=relative_path):
                path = REPO_ROOT / relative_path
                line_count = len(path.read_text(encoding="utf-8").splitlines())
                self.assertLessEqual(
                    line_count,
                    line_cap,
                    msg=(
                        f"{relative_path} grew to {line_count} lines; move new behavior "
                        "into a domain module or deliberately lower the architecture ratchet"
                    ),
                )


if __name__ == "__main__":
    unittest.main()
