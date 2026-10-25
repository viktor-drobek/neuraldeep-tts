"""Documentation contract for this neuraldeep-* repository.

This file is shared by every neuraldeep-* repository in the guarded API-skill
family and must stay identical across them. It checks the manifest, the
documentation claims, and the native rule scopes. It does not import the
helper: the script tests live in the repository's own suite and run once the
helper exists.
"""

import re
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def frontmatter():
    """Return (name, version, description) from the SKILL.md frontmatter."""
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None, None, None
    end = text.index("\n---\n", 4)
    head = text[4:end]
    name = version = description = None
    lines = head.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        key, sep, value = line.partition(":")
        if not sep:
            index += 1
            continue
        key, value = key.strip(), value.strip()
        if key == "name":
            name = value.strip().strip("\"'")
        elif key == "version" and line.startswith("  "):
            version = value.strip().strip("\"'")
        elif key == "description":
            if value:
                description = value.strip().strip("\"'")
            else:
                parts = []
                index += 1
                while index < len(lines):
                    item = lines[index]
                    if item.startswith("  ") and item.strip():
                        parts.append(item.strip())
                        index += 1
                        continue
                    break
                description = " ".join(parts)
        index += 1
    return name, version, description


def service_name():
    """Return the repository name implied by the checkout directory."""
    return ROOT.name


class ContractTests(unittest.TestCase):
    def test_manifest_name_matches_the_repository(self):
        name, _, _ = frontmatter()
        self.assertEqual(name, service_name())

    def test_manifest_declares_version_1_0_0(self):
        _, version, _ = frontmatter()
        self.assertIsNotNone(version, "SKILL.md has no metadata.version")
        self.assertRegex(version, SEMVER)
        self.assertEqual(version, "1.0.0")

    def test_manifest_description_is_present_and_autonomous(self):
        _, _, description = frontmatter()
        self.assertTrue(description, "SKILL.md has no description")
        self.assertGreaterEqual(len(description), 40)
        self.assertIn("blocked", description.lower())

    def test_required_files_exist(self):
        for name in (
            "SKILL.md",
            "STARTER_RELAY.md",
            "README.md",
            "TESTING.md",
            "AGENTS.md",
            "LICENSE",
            ".gitignore",
            "CLAUDE.md",
        ):
            self.assertTrue((ROOT / name).is_file(), f"missing {name}")
        for name in ("workflow", "client", "documentation"):
            self.assertTrue((ROOT / ".cursor" / "rules" / f"{name}.mdc").is_file())
            self.assertTrue((ROOT / ".claude" / "rules" / f"{name}.md").is_file())
            self.assertTrue((ROOT / ".coddy" / "rules" / f"{name}.md").is_file())
        self.assertTrue((ROOT / ".codex" / "hooks.json").is_file())
        self.assertTrue((ROOT / ".codex" / "hooks" / "attach_rules.py").is_file())

    def test_claude_md_is_a_symlink_to_agents_md(self):
        claude_md = ROOT / "CLAUDE.md"
        self.assertTrue(claude_md.is_symlink())
        self.assertEqual(__import__("os").readlink(claude_md), "AGENTS.md")

    def test_blocked_is_documented_as_final(self):
        for name in ("SKILL.md", "STARTER_RELAY.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("blocked", text, f"{name} does not document blocked")

    def test_no_direct_call_advice(self):
        for name in ("SKILL.md", "STARTER_RELAY.md", "README.md", "AGENTS.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("curl https://api.neuraldeep.ru", text)
            self.assertNotIn("curl https://drift.neuraldeep.ru", text)
            self.assertNotIn("NEURALDEEP_API_KEY=$(jq", text)

    def test_cli_flags_are_documented(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        for flag in ("--payload", "--state", "--output", "--wallet-limit"):
            self.assertIn(flag, text, f"SKILL.md does not document {flag}")

    def test_test_directory_has_the_contract_and_example_tests(self):
        for name in ("test_contract.py", "test_examples.py"):
            self.assertTrue((ROOT / "tests" / name).is_file())


if __name__ == "__main__":
    unittest.main()
