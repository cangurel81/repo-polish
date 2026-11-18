"""Unit tests for repo-polish. No network, no gh CLI, no git required.

Run: python -m unittest discover -s test -v
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)

import repo_polish as rp  # noqa: E402


LONG_README = """# Demo tool

A small command line tool that does one thing. It reads a file, checks it, and
prints a report. Written for people who want a quick answer without installing
a stack of dependencies.

## Install

    pip install demo-tool

## Usage

    demo-tool check ./project

The exit code is 0 when the project is clean and 1 when it has findings.

## Licence

MIT
"""

MIT_TEXT = """MIT License

Copyright (c) 2026 Someone

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction.
"""

GPL3_TEXT = """GNU GENERAL PUBLIC LICENSE
Version 3, 29 June 2007

Copyright (C) 2007 Free Software Foundation, Inc.
"""

AGPL3_TEXT = """GNU AFFERO GENERAL PUBLIC LICENSE
Version 3, 19 November 2007

Copyright (C) 2007 Free Software Foundation, Inc.
"""


class Fixture:
    """Throwaway repo tree on disk."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="rp-test-")

    def write(self, rel, text, encoding="utf-8", newline="\n"):
        path = os.path.join(self.root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if isinstance(text, bytes):
            with open(path, "wb") as fh:
                fh.write(text)
        else:
            with open(path, "w", encoding=encoding, newline=newline) as fh:
                fh.write(text)
        return path

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


def clean_fixture():
    fx = Fixture()
    fx.write("README.md", LONG_README)
    fx.write("LICENSE", MIT_TEXT)
    fx.write(".gitignore", "__pycache__/\n*.pyc\ndist/\nnode_modules/\n")
    return fx


def run_audit(fx, **kw):
    """Audit a fixture offline and return (report, checks-that-fired)."""
    ctx = rp.Ctx(fx.root, owner="someone", name="demo-tool", offline=True, **kw)
    report = rp.audit(ctx)
    return report, {r["check"] for r in report.rows}


def finding(report, check):
    return [r for r in report.rows if r["check"] == check]


class TestLicenseDetection(unittest.TestCase):
    def test_mit(self):
        self.assertEqual(rp.detect_license(MIT_TEXT), "MIT")

    def test_gpl3(self):
        self.assertEqual(rp.detect_license(GPL3_TEXT), "GPL-3.0")

    def test_agpl_wins_over_gpl(self):
        # The AGPL body also contains "GNU General Public License", so the
        # AGPL check has to run first or every AGPL repo is mislabelled.
        self.assertEqual(rp.detect_license(AGPL3_TEXT), "AGPL-3.0")

    def test_gpl3_mentioning_affero_in_the_body_stays_gpl3(self):
        # Real GPL-3.0 section 13 says "you may also combine it with the GNU
        # Affero General Public License". A whole-body substring search
        # reported all five GPL-3.0 repos in this workspace as AGPL-3.0.
        body = GPL3_TEXT + "\n13. Use with the GNU Affero General Public License.\n"
        self.assertEqual(rp.detect_license(body), "GPL-3.0")

    def test_agpl_title_after_a_copyright_line_is_found(self):
        text = "Copyright (c) 2026 Someone\n\n" + AGPL3_TEXT
        self.assertEqual(rp.detect_license(text), "AGPL-3.0")

    def test_unknown(self):
        self.assertIsNone(rp.detect_license("All rights reserved, do not copy."))

    def test_alias_does_not_match_inside_a_word(self):
        # "mit" lives inside "commit", "limit" and "submit"; three GPL-3.0
        # READMEs that never mention MIT were flagged as saying MIT.
        self.assertEqual(rp.license_mentions("Fix the commit limit, then submit"), [])
        self.assertEqual(rp.license_mentions("Licensed under the MIT License"),
                         ["MIT"])
        self.assertEqual(rp.license_mentions("See LICENSE (GPL-3.0)."),
                         ["GPL-3.0"])

    def test_word_containing_mit_does_not_create_a_mismatch(self):
        fx = Fixture()
        try:
            fx.write("LICENSE", GPL3_TEXT)
            fx.write("README.md", LONG_README.replace("MIT", "GPL-3.0")
                     + "\nCommit early, limit the scope, submit the patch.\n")
            _, checks = run_audit(fx)
            self.assertNotIn("license.mismatch", checks)
        finally:
            fx.cleanup()

    def test_readme_licence_mismatch_is_an_error(self):
        fx = clean_fixture()
        try:
            fx.write("README.md", LONG_README.replace("MIT", "GPL-3.0"))
            report, checks = run_audit(fx)
            self.assertIn("license.mismatch", checks)
            self.assertTrue(finding(report, "license.mismatch")[0]["message"]
                            .startswith("LICENSE file is MIT"))
        finally:
            fx.cleanup()

    def test_matching_licence_is_quiet(self):
        fx = clean_fixture()
        try:
            _, checks = run_audit(fx)
            self.assertNotIn("license.mismatch", checks)
        finally:
            fx.cleanup()

    def test_other_licences_named_alongside_the_real_one_are_a_reference(self):
        # The profile README ends with "MIT for most, GPL-3.0 or AGPL-3.0 where
        # noted" and has an MIT LICENSE file. Naming both is not a claim that
        # this repo is GPL, so it must not fail the audit.
        fx = clean_fixture()
        try:
            fx.write("README.md", LONG_README
                     + "\nEach repository carries its own licence - MIT for most, "
                       "GPL-3.0 or AGPL-3.0 where noted in its LICENSE file.\n")
            report, checks = run_audit(fx)
            self.assertNotIn("license.mismatch", checks)
            self.assertIn("license.other-mentions", checks)
            self.assertEqual(finding(report, "license.mismatch"), [])
        finally:
            fx.cleanup()


class TestEncoding(unittest.TestCase):
    def test_bom_is_flagged(self):
        fx = clean_fixture()
        try:
            fx.write("notes.md", b"\xef\xbb\xbf# Notlar\n\nT\xc3\xbcrk\xc3\xa7e metin.\n")
            report, checks = run_audit(fx)
            self.assertIn("encoding.bom", checks)
            self.assertEqual(finding(report, "encoding.bom")[0]["path"], "notes.md")
        finally:
            fx.cleanup()

    def test_bom_strip_rewrites_the_file(self):
        fx = clean_fixture()
        try:
            path = fx.write("notes.md", b"\xef\xbb\xbf# Notlar\n")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = rp.main(["bom", fx.root, "--fix"])
            self.assertEqual(rc, rp.EXIT_CLEAN)
            with open(path, "rb") as fh:
                self.assertFalse(fh.read().startswith(b"\xef\xbb\xbf"))
        finally:
            fx.cleanup()

    def test_bom_without_fix_reports_and_exits_1(self):
        fx = clean_fixture()
        try:
            fx.write("notes.md", b"\xef\xbb\xbf# Notlar\n")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = rp.main(["bom", fx.root])
            self.assertEqual(rc, rp.EXIT_ISSUES)
        finally:
            fx.cleanup()

    def test_mojibake_is_flagged(self):
        fx = clean_fixture()
        try:
            fx.write("notes.md", "# Ba\u015fl\u0131k\n\nT\u00fcrk\u00e7e: g\u00fczel\n")
            # cp437 read of the UTF-8 bytes for "ü" is the classic ├╝ damage
            fx.write("broken.md", "# Ba\u015fl\u0131k\n\nT\u00fcrk\u251c\u255de metin\n")
            report, checks = run_audit(fx)
            self.assertIn("encoding.mojibake", checks)
            self.assertEqual(finding(report, "encoding.mojibake")[0]["path"], "broken.md")
        finally:
            fx.cleanup()

    def test_cp1254_mojibake_is_flagged(self):
        fx = clean_fixture()
        try:
            # cp1254 read of the UTF-8 bytes for "ü" is "Ã¼"
            fx.write("broken.md", "T\u00fcrk\u00c3\u00bce metin\n")
            _, checks = run_audit(fx)
            self.assertIn("encoding.mojibake", checks)
        finally:
            fx.cleanup()

    def test_single_box_drawing_char_is_not_mojibake(self):
        # Regression: ├ ─ └ are legitimate ASCII-art tree characters. Flagging
        # them made every README with a directory tree a false positive.
        fx = clean_fixture()
        try:
            fx.write("README.md", LONG_README + "\n```\nrepo\n\u251c\u2500\u2500 src/\n"
                                                "\u2514\u2500\u2500 README.md\n```\n")
            _, checks = run_audit(fx)
            self.assertNotIn("encoding.mojibake", checks)
        finally:
            fx.cleanup()

    def test_invalid_utf8_is_flagged(self):
        fx = clean_fixture()
        try:
            fx.write("latin.md", b"caf\xe9 not utf-8\n")
            _, checks = run_audit(fx)
            self.assertIn("encoding.not-utf8", checks)
        finally:
            fx.cleanup()


class TestReadme(unittest.TestCase):
    def test_missing_readme(self):
        fx = Fixture()
        try:
            fx.write("LICENSE", MIT_TEXT)
            _, checks = run_audit(fx)
            self.assertIn("readme.present", checks)
        finally:
            fx.cleanup()

    def test_thin_readme(self):
        fx = clean_fixture()
        try:
            fx.write("README.md", "# demo\n\nTiny.\n")
            _, checks = run_audit(fx)
            self.assertIn("readme.thin", checks)
        finally:
            fx.cleanup()

    def test_broken_relative_link(self):
        fx = clean_fixture()
        try:
            fx.write("README.md", LONG_README + "\nSee [the guide](docs/guide.md).\n")
            report, checks = run_audit(fx)
            self.assertIn("readme.relative-link", checks)
            self.assertIn("docs/guide.md", finding(report, "readme.relative-link")[0]["message"])
        finally:
            fx.cleanup()

    def test_relative_link_with_escaped_space_resolves(self):
        fx = clean_fixture()
        try:
            fx.write("docs/my file.md", "# guide\n")
            fx.write("README.md", LONG_README + "\nSee [the guide](docs/my%20file.md).\n")
            _, checks = run_audit(fx)
            self.assertNotIn("readme.relative-link", checks)
        finally:
            fx.cleanup()

    def test_versioned_download_asset_is_a_warning(self):
        fx = clean_fixture()
        try:
            fx.write("README.md", LONG_README +
                     "\n[zip](https://github.com/someone/demo-tool/releases/latest/download/demo-tool-v1.2.3.zip)\n")
            report, checks = run_audit(fx)
            self.assertIn("readme.versioned-asset", checks)
            self.assertEqual(finding(report, "readme.versioned-asset")[0]["level"], "warn")
        finally:
            fx.cleanup()

    def test_versionless_download_asset_is_quiet(self):
        fx = clean_fixture()
        try:
            fx.write("README.md", LONG_README +
                     "\n[zip](https://github.com/someone/demo-tool/releases/latest/download/demo-tool.zip)\n")
            _, checks = run_audit(fx)
            self.assertNotIn("readme.versioned-asset", checks)
        finally:
            fx.cleanup()

    def test_repeated_download_link_is_reported_once(self):
        fx = clean_fixture()
        try:
            link = ("\n[zip](https://github.com/someone/demo-tool/releases/latest/"
                    "download/demo-tool-v1.2.3.zip)\n")
            fx.write("README.md", LONG_README + link + link)
            report, checks = run_audit(fx)
            self.assertEqual(len(finding(report, "readme.versioned-asset")), 1)
        finally:
            fx.cleanup()

    def test_placeholder_is_flagged(self):
        fx = clean_fixture()
        try:
            fx.write("README.md", LONG_README + "\nContact: your-username on GitHub.\n")
            _, checks = run_audit(fx)
            self.assertIn("readme.placeholder", checks)
        finally:
            fx.cleanup()


class TestTree(unittest.TestCase):
    def test_generated_directory(self):
        fx = clean_fixture()
        try:
            fx.write(".gitignore", "node_modules/\n")
            fx.write("__pycache__/mod.cpython-313.pyc", b"\x00\x01")
            report, checks = run_audit(fx)
            self.assertIn("tree.junk-dir", checks)
            self.assertIn("__pycache__", finding(report, "tree.junk-dir")[0]["message"])
        finally:
            fx.cleanup()

    def test_ignored_cache_directory_is_not_a_finding(self):
        # A __pycache__ folder that .gitignore already covers never reaches
        # GitHub, so flagging it would be noise.
        fx = clean_fixture()
        try:
            fx.write("__pycache__/mod.cpython-313.pyc", b"\x00\x01")
            _, checks = run_audit(fx)
            self.assertNotIn("tree.junk-dir", checks)
            self.assertNotIn("tree.binary", checks)
        finally:
            fx.cleanup()

    def test_binary_in_tree(self):
        fx = clean_fixture()
        try:
            fx.write("app.exe", b"MZ\x90\x00")
            _, checks = run_audit(fx)
            self.assertIn("tree.binary", checks)
        finally:
            fx.cleanup()

    def test_bin_source_folder_is_not_junk(self):
        # Regression: bin/ holds source in most Node and Python CLIs.
        fx = clean_fixture()
        try:
            fx.write("bin/tool.mjs", "#!/usr/bin/env node\nconsole.log('hi')\n")
            _, checks = run_audit(fx)
            self.assertNotIn("tree.junk-dir", checks)
        finally:
            fx.cleanup()

    def test_missing_gitignore(self):
        fx = Fixture()
        try:
            fx.write("README.md", LONG_README)
            fx.write("LICENSE", MIT_TEXT)
            _, checks = run_audit(fx)
            self.assertIn("gitignore.missing", checks)
        finally:
            fx.cleanup()

    def test_incomplete_gitignore(self):
        fx = clean_fixture()
        try:
            fx.write("tool.py", "print('hi')\n")
            fx.write(".gitignore", "node_modules/\n")
            report, checks = run_audit(fx)
            self.assertIn("gitignore.incomplete", checks)
            message = finding(report, "gitignore.incomplete")[0]["message"]
            self.assertIn("__pycache__", message)
        finally:
            fx.cleanup()

    def test_node_repo_is_not_asked_for_python_patterns(self):
        # A Node project has no __pycache__; asking for it is noise.
        fx = clean_fixture()
        try:
            fx.write("bin/tool.mjs", "#!/usr/bin/env node\nconsole.log('hi')\n")
            fx.write(".gitignore", "node_modules/\n*.log\n")
            _, checks = run_audit(fx)
            self.assertNotIn("gitignore.incomplete", checks)
        finally:
            fx.cleanup()


class TestWorkflows(unittest.TestCase):
    def test_eol_node_in_matrix(self):
        fx = clean_fixture()
        try:
            fx.write(".github/workflows/ci.yml",
                     "jobs:\n  test:\n    strategy:\n      matrix:\n"
                     "        node-version: [18, 22]\n    steps:\n"
                     "      - uses: actions/checkout@v4\n")
            report, checks = run_audit(fx)
            self.assertIn("ci.eol-node", checks)
            msg = finding(report, "ci.eol-node")[0]["message"]
            self.assertIn("Node 18", msg)
            self.assertNotIn("Node 22", msg)
        finally:
            fx.cleanup()

    def test_supported_node_is_quiet(self):
        fx = clean_fixture()
        try:
            fx.write(".github/workflows/ci.yml",
                     "jobs:\n  test:\n    strategy:\n      matrix:\n"
                     "        node-version: [22, 24]\n    steps:\n"
                     "      - uses: actions/checkout@v4\n")
            _, checks = run_audit(fx)
            self.assertNotIn("ci.eol-node", checks)
        finally:
            fx.cleanup()

    def test_unpinned_action(self):
        fx = clean_fixture()
        try:
            fx.write(".github/workflows/ci.yml",
                     "jobs:\n  test:\n    steps:\n      - uses: actions/checkout\n")
            _, checks = run_audit(fx)
            self.assertIn("ci.unpinned-action", checks)
        finally:
            fx.cleanup()

    def test_floating_branch_action(self):
        fx = clean_fixture()
        try:
            fx.write(".github/workflows/ci.yml",
                     "jobs:\n  test:\n    steps:\n      - uses: actions/setup-node@main\n")
            _, checks = run_audit(fx)
            self.assertIn("ci.floating-action", checks)
        finally:
            fx.cleanup()

    def test_no_workflows_is_a_note(self):
        fx = clean_fixture()
        try:
            _, checks = run_audit(fx)
            self.assertIn("ci.none", checks)
        finally:
            fx.cleanup()


class TestIgnoreFile(unittest.TestCase):
    def test_listed_file_is_skipped_by_encoding_checks(self):
        # A repo that documents what mojibake looks like has to quote the
        # damaged characters; quoting them must not fail its own audit.
        fx = clean_fixture()
        try:
            fx.write("notes.md", "T\u00fcrk\u00c3\u00bce metin\n")
            fx.write(".repo-polish-ignore", "# examples only\nnotes.md\n")
            _, checks = run_audit(fx)
            self.assertNotIn("encoding.mojibake", checks)
        finally:
            fx.cleanup()

    def test_unlisted_file_still_flagged(self):
        fx = clean_fixture()
        try:
            fx.write("notes.md", "T\u00fcrk\u00c3\u00bce metin\n")
            fx.write("other.md", "T\u00fcrk\u00c3\u00bce metin\n")
            fx.write(".repo-polish-ignore", "notes.md\n")
            report, _ = run_audit(fx)
            paths = [r.get("path") for r in finding(report, "encoding.mojibake")]
            self.assertEqual(paths, ["other.md"])
        finally:
            fx.cleanup()


class TestTopics(unittest.TestCase):
    def test_valid_list(self):
        self.assertEqual(rp.validate_topics(["python", "cli", "github"]), [])

    def test_too_many(self):
        problems = rp.validate_topics(["t%d" % i for i in range(21)])
        self.assertTrue(any("max 20" in p for p in problems))

    def test_uppercase(self):
        self.assertTrue(any("lowercase" in p for p in rp.validate_topics(["Python"])))

    def test_space(self):
        self.assertTrue(any("space" in p for p in rp.validate_topics(["code quality"])))

    def test_underscore(self):
        self.assertTrue(any("underscore" in p for p in rp.validate_topics(["code_quality"])))

    def test_too_long(self):
        self.assertTrue(any("50" in p for p in rp.validate_topics(["x" * 51])))

    def test_duplicate(self):
        self.assertTrue(any("duplicate" in p for p in rp.validate_topics(["cli", "cli"])))

    def test_empty(self):
        self.assertTrue(rp.validate_topics([""]))


class TestGhCall(unittest.TestCase):
    """The gh argv, without running gh."""

    def _capture(self, **kwargs):
        captured = {}
        real_run = rp.subprocess.run

        class Fake:
            returncode = 0
            stdout = b"{}"
            stderr = b""

        def fake_run(cmd, **kw):
            captured["cmd"] = list(cmd)
            return Fake()

        rp.subprocess.run = fake_run
        try:
            rp.Gh("gh").call(["repos/a/b"], payload={"x": 1}, **kwargs)
        finally:
            rp.subprocess.run = real_run
        return captured["cmd"]

    def test_default_method_is_patch(self):
        cmd = self._capture()
        self.assertEqual(cmd.count("-X"), 1)
        self.assertEqual(cmd[cmd.index("-X") + 1], "PATCH")

    def test_topics_put_is_not_overridden_by_patch(self):
        # Two -X flags in one argv means the last one wins, which turned a
        # topics PUT into a PATCH and 404'd on the real API.
        cmd = self._capture(method="PUT")
        self.assertEqual(cmd.count("-X"), 1)
        self.assertEqual(cmd[cmd.index("-X") + 1], "PUT")
        self.assertNotIn("PATCH", cmd)


class TestMeta(unittest.TestCase):
    """check_meta against a fake API payload, no gh, no network."""

    def _report(self, name, owner, **meta):
        fx = clean_fixture()
        try:
            ctx = rp.Ctx(fx.root, owner=owner, name=name, offline=True)
            ctx.gh = object()  # truthy stand-in; ctx.api is stubbed below
            payload = {"description": "Indexes and searches a skill catalogue",
                       "homepage": "", "topics": ["python", "cli", "tooling"]}
            payload.update(meta)
            ctx.api = lambda suffix: payload
            rp.check_meta(ctx)
            return ctx.report
        finally:
            fx.cleanup()

    def test_missing_homepage_warns_on_a_normal_repo(self):
        report = self._report("skill-router", "cangurel81")
        self.assertIn("meta.homepage", {r["check"] for r in report.rows})

    def test_profile_repo_does_not_warn_about_homepage(self):
        # <owner>/<owner> has no releases and no package, so the homepage hint
        # is noise there; the repo showed up as the only warning in a 12-repo
        # sweep because of it.
        report = self._report("cangurel81", "cangurel81")
        checks = {r["check"] for r in report.rows}
        self.assertNotIn("meta.homepage", checks)
        self.assertIn("meta.homepage-skipped", checks)

    def test_profile_detection_is_case_insensitive(self):
        fx = clean_fixture()
        try:
            ctx = rp.Ctx(fx.root, owner="CanGurel", name="cangurel", offline=True)
            self.assertTrue(rp.is_profile_repo(ctx))
            ctx.name = "something-else"
            self.assertFalse(rp.is_profile_repo(ctx))
            ctx.owner = None
            self.assertFalse(rp.is_profile_repo(ctx))
        finally:
            fx.cleanup()


class TestCli(unittest.TestCase):
    def test_clean_repo_exits_zero(self):
        fx = clean_fixture()
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = rp.main(["audit", fx.root, "--offline"])
            self.assertEqual(rc, rp.EXIT_CLEAN)
        finally:
            fx.cleanup()

    def test_error_exits_one(self):
        fx = Fixture()
        try:
            fx.write("LICENSE", MIT_TEXT)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = rp.main(["audit", fx.root, "--offline"])
            self.assertEqual(rc, rp.EXIT_ISSUES)
        finally:
            fx.cleanup()

    def test_fail_on_warn(self):
        fx = clean_fixture()
        try:
            fx.write(".gitignore", "node_modules/\n")  # only produces a note
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = rp.main(["audit", fx.root, "--offline", "--fail-on", "warn"])
            self.assertEqual(rc, rp.EXIT_CLEAN)  # note is below warn

            fx.write("README.md", LONG_README + "\nContact: your-username\n")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = rp.main(["audit", fx.root, "--offline", "--fail-on", "warn"])
            self.assertEqual(rc, rp.EXIT_ISSUES)
        finally:
            fx.cleanup()

    def test_json_output_is_valid(self):
        fx = clean_fixture()
        try:
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(io.StringIO()):
                rp.main(["audit", fx.root, "--offline", "--json"])
            payload = json.loads(buf.getvalue())
            self.assertEqual(len(payload), 1)
            self.assertIn("findings", payload[0])
            self.assertEqual(payload[0]["errors"], 0)
        finally:
            fx.cleanup()

    def test_missing_folder_exits_two(self):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = rp.main(["audit", os.path.join(tempfile.gettempdir(), "rp-does-not-exist"),
                          "--offline"])
        self.assertEqual(rc, rp.EXIT_ERROR)

    def test_meta_apply_rejects_bad_topics_before_network(self):
        fx = clean_fixture()
        try:
            spec = fx.write("meta.json", json.dumps({
                "repo": "someone/demo-tool",
                "topics": ["Bad Topic"],
            }))
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = rp.main(["meta", "--apply", spec])
            self.assertEqual(rc, rp.EXIT_ISSUES)
        finally:
            fx.cleanup()


if __name__ == "__main__":
    unittest.main()
