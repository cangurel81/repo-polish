#!/usr/bin/env python3
"""repo-polish - find the defects that make a GitHub repository read as abandoned.

Standard library only, Python 3.8+.

Local checks need nothing but the folder: README, encoding, junk files,
.gitignore, LICENSE, CI workflows.
Remote checks need the `gh` CLI. If it is missing they are reported as
skipped instead of failing, so the tool still works on a bare machine.

    repo-polish audit .                     # audit the repo in the current folder
    repo-polish audit --all                 # every public repo of the logged-in user
    repo-polish audit . --json              # machine-readable
    repo-polish bom . --fix                 # strip UTF-8 BOM in place
    repo-polish meta . --apply meta.json    # write description/homepage/topics

Exit codes: 0 clean, 1 findings at or above --fail-on, 2 tool error.
"""

import argparse
import concurrent.futures
import datetime
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

VERSION = "1.0.0"
USER_AGENT = "repo-polish/%s" % VERSION

EXIT_CLEAN, EXIT_ISSUES, EXIT_ERROR = 0, 1, 2
SEVERITY = {"error": 3, "warn": 2, "info": 1}

TEXT_EXTS = {
    ".md", ".markdown", ".txt", ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx",
    ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".css", ".html", ".htm",
    ".php", ".sh", ".ps1", ".bat", ".cmd", ".rb", ".go", ".rs", ".java", ".kt",
    ".svg", ".xml", ".spec", ".editorconfig",
}

# Generated artifacts that should never be tracked. Binaries belong in releases.
# `bin` is deliberately absent: it is a source folder in most Node and Python
# CLIs, so flagging it produces a false positive on exactly the repos this tool
# is meant to help. Build output is caught by JUNK_EXTS instead.
JUNK_DIRS = {
    "__pycache__", "node_modules", ".venv", "venv", "env", "dist", "build",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".next", ".nuxt", "target",
    ".gradle", ".tox", ".cache",
}
JUNK_EXTS = {
    ".pyc", ".pyo", ".pyd", ".class", ".o", ".obj",
    ".exe", ".msi", ".dll", ".so", ".dylib",
    ".zip", ".xpi", ".crx", ".whl", ".tar", ".gz", ".tgz", ".bz2", ".xz",
    ".7z", ".rar", ".iso", ".dmg", ".appimage", ".deb", ".rpm",
}

# Multi-character cp1254/UTF-8 mojibake sequences only.
# Single box-drawing characters (├ ─ └) are legitimate in tree diagrams,
# so flagging them produces false positives on every ASCII-art README.
# Written as escapes on purpose: a literal copy of these characters is exactly
# the corruption the tool is meant to find, so it must not round-trip through
# an editor that "fixes" them.
MOJIBAKE = [
    "\ufffd",            # U+FFFD, real data loss
    "\u251c\u255d",      # cp437 read of a UTF-8 u-umlaut
    "\u251c\u011f",      # cp437 read of a UTF-8 g-breve
    "\u251c\u00ba",
    "\u251c\u2013",
    "\u251c\u2014",
    "\u251c\u00a7",
    "\u251c\u00b6",
    "\u251c\u00bc",
    "\u251c\u00ff",
    "\u253c",
    "\u00d4\u00c7",      # cp1254 read of a UTF-8 en dash
    "\u00c3\u00bc",
    "\u00c3\u00a7",
    "\u00c4\u00b1",
    "\u00c5\u0178",
    "\u00c4\u0178",
    "\u00c3\u00b6",
    "\u00f0\u0178",      # cp1254 read of a UTF-8 emoji prefix
    "\u00c3\u00a2",
    "\u00c3\u00ae",
]

# Description adjectives that say nothing about what the software does.
FILLER = [
    "powerful", "amazing", "awesome", "blazing", "blazingly", "revolutionary",
    "cutting-edge", "cutting edge", "state-of-the-art", "world-class",
    "best-in-class", "seamless", "effortless", "next-generation", "game-changing",
    "all-in-one", "one-stop", "unlimited", "ultimate", "supercharged",
    "industry-leading", "enterprise-grade", "military-grade",
]

# Node majors and the day their maintenance window closed.
# Checked against today's date so the table does not go stale.
NODE_EOL = {
    12: "2022-04-30", 14: "2023-04-30", 16: "2023-09-11",
    18: "2025-04-30", 20: "2026-04-30", 22: "2027-04-30", 24: "2028-04-30",
}

# Hosts that answer 403/429 to automated clients while working fine in a browser.
BOT_BLOCKING_HOSTS = (
    "npmjs.com", "npmjs.org", "pypi.org", "packagist.org", "crates.io",
    "marketplace.visualstudio.com", "addons.mozilla.org", "chrome.google.com",
    "hub.docker.com", "linkedin.com", "x.com", "twitter.com",
)

# Hosts that exist only as examples in documentation. A README that shows a
# URL shape has to use one of these, so they are never requested.
PLACEHOLDER_HOSTS = (
    "example.com", "example.org", "example.net", "localhost", "127.0.0.1",
    "your-domain.com", "yourdomain.com", "your-username.github.io",
)

DOWNLOAD_RE = re.compile(r"releases/latest/download/([^\s)\]\"'<>]+)")
MD_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")
CODE_BLOCK_RE = re.compile(r"```.*?```", re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
HTTP_RE = re.compile(r"https?://[^\s)\]\"'<>]+")
VERSION_IN_NAME_RE = re.compile(r"\d+\.\d+")


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

class Report:
    def __init__(self):
        self.rows = []

    def add(self, level, check, message, path=None, fix=None):
        self.rows.append({
            "level": level, "check": check, "message": message,
            "path": path, "fix": fix,
        })

    def error(self, check, message, **kw):
        self.add("error", check, message, **kw)

    def warn(self, check, message, **kw):
        self.add("warn", check, message, **kw)

    def info(self, check, message, **kw):
        self.add("info", check, message, **kw)

    def count(self, level):
        return sum(1 for r in self.rows if r["level"] == level)

    def worst(self):
        if self.count("error"):
            return "error"
        if self.count("warn"):
            return "warn"
        return "info"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def run(args, cwd=None, timeout=120):
    """Run a command and return (returncode, stdout-bytes, stderr-text)."""
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, b"", str(exc)[:300]
    return p.returncode, p.stdout, p.stderr.decode("utf-8", "replace")[:300]


def find_gh():
    """Locate the gh CLI. GH_BIN wins, then PATH, then the usual install dirs."""
    env = os.environ.get("GH_BIN")
    if env and os.path.isfile(env):
        return env
    found = shutil.which("gh")
    if found:
        return found
    for cand in (
        r"C:\Users\Public\gh\gh.exe",
        os.path.expanduser(r"~\scoop\shims\gh.exe"),
        "/usr/local/bin/gh",
        "/opt/homebrew/bin/gh",
    ):
        if os.path.isfile(cand):
            return cand
    return None


class Gh:
    """Thin wrapper around `gh api`. Output is decoded as UTF-8 bytes.

    Never pipe gh through PowerShell redirection: on a Turkish Windows console
    that produces UTF-16 files with cp1254 damage.
    """

    def __init__(self, binary):
        self.binary = binary

    def json(self, path, timeout=90):
        rc, out, err = run([self.binary, "api", path], timeout=timeout)
        if rc != 0:
            return None
        try:
            return json.loads(out.decode("utf-8", "replace"))
        except ValueError:
            return None

    def call(self, args, payload=None, method="PATCH", timeout=90):
        """Call `gh api`. The method lives here, not in `args`.

        Passing "-X PUT" inside args while also passing a payload produced
        `-X PUT ... -X PATCH`, and gh honours the last one — a topics PUT
        silently became a PATCH and 404'd.
        """
        cmd = [self.binary, "api"] + list(args)
        if payload is not None:
            cmd += ["-X", method, "--input", "-"]
        p = subprocess.run(cmd, input=json.dumps(payload).encode("utf-8")
                           if payload is not None else None,
                           capture_output=True, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "replace"), \
            p.stderr.decode("utf-8", "replace")


def head(url, timeout=25):
    """HEAD a URL. Some hosts reject HEAD, so fall back to a 1-byte GET."""
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 405, 501):
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return r.status
            except urllib.error.HTTPError as exc2:
                return exc2.code
            except Exception:
                return exc.code
        return exc.code
    except Exception:
        return 0


def strip_code(text):
    """Drop fenced blocks and inline code spans.

    A README that documents the tool (or quotes a broken example) writes URLs
    inside code. Scanning those as live links reports the documentation as a
    defect, so every link scan runs on the prose only.
    """
    return INLINE_CODE_RE.sub(" ", CODE_BLOCK_RE.sub(" ", text))


def is_placeholder_url(url):
    """True for hosts that exist only as examples; HEAD-ing them proves nothing."""
    netloc = urllib.parse.urlsplit(url).netloc.lower()
    host = netloc.rsplit("@", 1)[-1].split(":")[0]
    if host in PLACEHOLDER_HOSTS:
        return True
    return any(host.endswith("." + h) for h in PLACEHOLDER_HOSTS)


def read_bytes(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return b""


IGNORE_FILE = ".repo-polish-ignore"


def load_ignore(root):
    """Path globs from .repo-polish-ignore, one per line, `#` comments allowed.

    Only the encoding checks honour this. A repository that documents what
    mojibake looks like has to quote the damaged characters, and quoting them
    must not make the repository fail its own audit.
    """
    path = os.path.join(root, IGNORE_FILE)
    if not os.path.isfile(path):
        return []
    patterns = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                patterns.append(line)
    except OSError:
        return []
    return patterns


def ignored(rel, patterns):
    """Match a repo-relative path against the ignore globs."""
    rel = rel.replace(os.sep, "/")
    for pat in patterns:
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(os.path.basename(rel), pat):
            return True
    return False


def walk_files(root):
    """Every file on disk, minus .git and minus junk folders."""
    files = []
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in JUNK_DIRS and d != ".git"]
        for n in names:
            files.append(os.path.join(base, n))
    return files


def walk_all_files(root):
    """Every file on disk except inside .git — junk folders included."""
    files = []
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git"]
        for n in names:
            files.append(os.path.join(base, n))
    return files


def git_tracked(root):
    """Files git reports as tracked, or None when git is unavailable."""
    rc, out, _ = run(["git", "ls-files"], cwd=root, timeout=60)
    if rc != 0:
        return None
    return [os.path.join(root, line.decode("utf-8", "replace").strip()
                         .replace("/", os.sep))
            for line in out.splitlines() if line.strip()]


def load_gitignore(root):
    """Patterns from .gitignore. Only used when git is not available."""
    path = os.path.join(root, ".gitignore")
    if not os.path.isfile(path):
        return []
    patterns = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("!"):
                    continue
                patterns.append(line.lstrip("/").rstrip("/"))
    except OSError:
        return []
    return patterns


def covered_by_gitignore(rel, patterns):
    rel = rel.replace(os.sep, "/")
    parts = rel.split("/")
    for pat in patterns:
        if not pat:
            continue
        if fnmatch.fnmatch(rel, pat) or rel.startswith(pat + "/"):
            return True
        for part in parts:
            if fnmatch.fnmatch(part, pat):
                return True
    return False


def tracked_files(root):
    """git ls-files when available, otherwise a filtered walk."""
    files = git_tracked(root)
    if files is not None:
        return files
    return walk_files(root)


def detect_license(text):
    """Map a LICENSE body to an SPDX id.

    The earliest title wins, not the first substring found anywhere in the
    body. GPL-3.0 section 13 says "you may also combine it with the GNU Affero
    General Public License", so a plain body search labelled every GPL-3.0
    repo as AGPL-3.0 and told the user to rewrite READMEs that were correct.
    """
    head = " ".join(text[:4000].lower().split())

    titles = (
        ("gnu affero general public license", "AGPL-3.0"),
        ("gnu lesser general public license", "LGPL-3.0"),
        ("gnu general public license", None),
    )
    found = sorted((head.find(needle), spdx)
                   for needle, spdx in titles if head.find(needle) != -1)
    if found:
        _, spdx = found[0]
        if spdx:
            return spdx
        return "GPL-3.0" if "version 3" in head else "GPL-2.0"
    if "apache license" in head and "2.0" in head:
        return "Apache-2.0"
    if "permission is hereby granted, free of charge" in head:
        return "MIT"
    if "redistribution and use in source and binary forms" in head:
        return "BSD-3-Clause" if "neither the name" in head else "BSD-2-Clause"
    if "mozilla public license" in head:
        return "MPL-2.0"
    if "the unlicense" in head:
        return "Unlicense"
    return None


LICENSE_ALIASES = {
    "MIT": ("mit",),
    "GPL-3.0": ("gpl-3.0", "gplv3", "gpl v3", "gpl3"),
    "GPL-2.0": ("gpl-2.0", "gplv2", "gpl v2", "gpl2"),
    "AGPL-3.0": ("agpl-3.0", "agplv3", "agpl"),
    "LGPL-3.0": ("lgpl-3.0", "lgplv3"),
    "Apache-2.0": ("apache-2.0", "apache 2.0", "apache license 2"),
    "BSD-3-Clause": ("bsd-3-clause", "bsd 3"),
    "BSD-2-Clause": ("bsd-2-clause", "bsd 2"),
    "MPL-2.0": ("mpl-2.0", "mozilla public license 2"),
    "Unlicense": ("unlicense",),
}


def license_mentions(text):
    """SPDX ids named in a README, in the order they appear.

    Aliases match on word boundaries. A plain substring search found "mit"
    inside "commit", "limit" and "submit", so three GPL-3.0 repos whose README
    never mentions MIT were reported as README-says-MIT mismatches.
    """
    low = text.lower()
    hits = []
    for spdx, aliases in LICENSE_ALIASES.items():
        best = None
        for alias in aliases:
            pattern = r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])"
            m = re.search(pattern, low)
            if m and (best is None or m.start() < best):
                best = m.start()
        if best is not None:
            hits.append((best, spdx))
    return [spdx for _, spdx in sorted(hits)]


def validate_topics(topics):
    """Return a list of human-readable problems with a topic list.

    Pure function: no network, no filesystem. GitHub's limits are 20 topics,
    50 characters each, lowercase letters, digits and hyphens only.
    """
    problems = []
    if len(topics) > 20:
        problems.append("%d topics (max 20)" % len(topics))
    for t in topics:
        if not isinstance(t, str) or not t:
            problems.append("empty topic")
            continue
        if len(t) > 50:
            problems.append("topic over 50 chars: %s" % t)
        if t != t.lower():
            problems.append("topic is not lowercase: %s" % t)
        if " " in t:
            problems.append("topic contains a space: %s" % t)
        if "_" in t:
            problems.append("topic contains an underscore: %s" % t)
    seen = set()
    for t in topics:
        if isinstance(t, str) and t in seen:
            problems.append("duplicate topic: %s" % t)
        seen.add(t)
    return problems


def repo_identity(root):
    """owner/name from the origin remote, or (None, None)."""
    rc, out, _ = run(["git", "remote", "get-url", "origin"], cwd=root, timeout=30)
    if rc != 0:
        return None, None
    url = out.decode("utf-8", "replace").strip()
    m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", url)
    if not m:
        return None, None
    return m.group(1), m.group(2)


# --------------------------------------------------------------------------
# context
# --------------------------------------------------------------------------

class Ctx:
    def __init__(self, root, owner=None, name=None, offline=False,
                 check_links=False, max_links=200):
        self.root = os.path.abspath(root)
        self.report = Report()
        self.owner = owner
        self.name = name or os.path.basename(self.root)
        self.offline = offline
        self.check_links = check_links
        self.max_links = max_links
        gh_bin = None if offline else find_gh()
        self.gh = Gh(gh_bin) if gh_bin else None
        self.remote = {}

    @property
    def slug(self):
        return "%s/%s" % (self.owner, self.name) if self.owner else self.name

    def api(self, suffix):
        if not self.gh or not self.owner:
            return None
        key = suffix
        if key not in self.remote:
            self.remote[key] = self.gh.json("repos/%s/%s%s" % (self.owner, self.name, suffix))
        return self.remote[key]


def is_profile_repo(ctx):
    """True for the special <owner>/<owner> repo that renders the profile page."""
    return bool(ctx.owner) and ctx.name.lower() == ctx.owner.lower()


# --------------------------------------------------------------------------
# local checks
# --------------------------------------------------------------------------

def check_readme(ctx):
    r = ctx.report
    candidates = ["README.md", "README.markdown", "readme.md", "README.rst",
                  "README.txt", "README"]
    path = None
    for c in candidates:
        p = os.path.join(ctx.root, c)
        if os.path.isfile(p):
            path = p
            break
    if not path:
        r.error("readme.present", "no README at the repository root",
                fix="add README.md: what it does, how to install, how to run")
        return None

    raw = read_bytes(path)
    if raw.startswith(b"\xef\xbb\xbf"):
        r.error("readme.bom", "README starts with a UTF-8 BOM",
                path=os.path.basename(path),
                fix="strip the BOM: repo-polish bom . --fix")
    text = raw.decode("utf-8", "replace")
    if len(text.strip()) < 200:
        r.warn("readme.thin", "README is under 200 characters",
               path=os.path.basename(path),
               fix="say what it does, who it is for, and how to run it")

    # relative links and images must resolve inside the tree
    broken = []
    for target in MD_LINK_RE.findall(strip_code(text)):
        if target.startswith(("http://", "https://", "#", "mailto:", "tel:")):
            continue
        clean = target.split("#")[0].split("?")[0]
        if not clean:
            continue
        clean = urllib.request.unquote(clean)
        if not os.path.exists(os.path.join(ctx.root, clean.replace("/", os.sep))):
            broken.append(clean)
    for b in sorted(set(broken)):
        r.error("readme.relative-link", "relative link points at a missing file: %s" % b,
                path=os.path.basename(path),
                fix="fix the path or delete the link")

    # placeholders left behind, ignoring code: a README that documents the
    # check has to quote the markers it looks for.
    prose = strip_code(text)
    for pat, label in (("TODO", "TODO marker"), ("Lorem ipsum", "lorem ipsum"),
                       ("<your-name>", "template placeholder"),
                       ("your-username", "template placeholder"),
                       ("example.com/your", "template placeholder")):
        if pat.lower() in prose.lower():
            r.warn("readme.placeholder", "%s: %s" % (label, pat),
                   path=os.path.basename(path), fix="replace or remove before publishing")

    return text


def check_download_links(ctx, text):
    """Download links must name a real asset, and must not embed a version."""
    if not text:
        return
    r = ctx.report
    links = DOWNLOAD_RE.findall(strip_code(text))
    latest = ctx.api("/releases/latest")
    assets = {a["name"] for a in (latest or {}).get("assets", [])}

    if not links:
        if assets:
            r.warn("readme.no-download-link",
                   "latest release ships %d asset(s) but the README never links them"
                   % len(assets),
                   fix="add a Download section pointing at "
                       "releases/latest/download/<asset>")
        return

    seen = set()
    for raw in links:
        name = raw.rstrip(".,;:")
        if name in seen:
            continue
        seen.add(name)
        if assets and name not in assets:
            r.error("readme.dead-download",
                    "README links download/%s but the latest release has: %s"
                    % (name, ", ".join(sorted(assets)) or "(none)"),
                    fix="rename the link to an existing asset")
        if VERSION_IN_NAME_RE.search(name):
            r.warn("readme.versioned-asset",
                   "download link contains a version (%s); it will 404 on the "
                   "next release" % name,
                   fix="upload the asset without a version in the file name")


def check_encoding(ctx):
    r = ctx.report
    skip = load_ignore(ctx.root)
    for path in tracked_files(ctx.root):
        ext = os.path.splitext(path)[1].lower()
        base = os.path.basename(path)
        if ext not in TEXT_EXTS and base not in (".gitignore", ".gitattributes", "LICENSE"):
            continue
        if not os.path.isfile(path):
            continue
        rel = os.path.relpath(path, ctx.root)
        if skip and ignored(rel, skip):
            continue
        raw = read_bytes(path)
        if raw.startswith(b"\xef\xbb\xbf"):
            r.error("encoding.bom", "UTF-8 BOM", path=rel,
                    fix="repo-polish bom . --fix")
            raw = raw[3:]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            r.error("encoding.not-utf8", "not valid UTF-8: %s" % exc, path=rel,
                    fix="re-save as UTF-8 without BOM")
            continue
        for seq in MOJIBAKE:
            idx = text.find(seq)
            if idx != -1:
                snippet = text[max(0, idx - 25):idx + 25].replace("\n", " ")
                r.error("encoding.mojibake",
                        "mojibake %r: ...%s..." % (seq, snippet), path=rel,
                        fix="retype the damaged text; do not try to transcode it")
                break


def check_tree(ctx):
    """Junk in the tree, judged against what git would actually publish.

    With git available the file list comes from `git ls-files`, so a
    `__pycache__` folder that is already ignored is not a finding. Without git
    the disk is walked and `.gitignore` is applied by hand, which is the same
    question answered from a different source.
    """
    r = ctx.report
    files = git_tracked(ctx.root)
    if files is None:
        ignore = load_gitignore(ctx.root)
        files = [p for p in walk_all_files(ctx.root)
                 if not covered_by_gitignore(os.path.relpath(p, ctx.root), ignore)]

    junk_dirs, junk_files = set(), []
    for path in files:
        rel = os.path.relpath(path, ctx.root)
        parts = rel.replace(os.sep, "/").split("/")
        for i, part in enumerate(parts[:-1]):
            if part in JUNK_DIRS:
                junk_dirs.add("/".join(parts[:i + 1]))
        if os.path.splitext(path)[1].lower() in JUNK_EXTS:
            junk_files.append(rel)

    for d in sorted(junk_dirs):
        r.error("tree.junk-dir", "generated directory is tracked: %s" % d,
                fix="git rm -r --cached %s and add it to .gitignore" % d)
    for f in junk_files:
        r.warn("tree.binary", "binary or build artifact tracked: %s" % f,
               fix="ship it as a release asset, not in the tree")


def project_kinds(files):
    """Which toolchains the tracked files belong to."""
    kinds = set()
    for p in files:
        ext = os.path.splitext(p)[1].lower()
        if ext == ".py":
            kinds.add("python")
        elif ext in (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"):
            kinds.add("node")
    return kinds


def check_gitignore(ctx):
    r = ctx.report
    path = os.path.join(ctx.root, ".gitignore")
    if not os.path.isfile(path):
        r.warn("gitignore.missing", "no .gitignore",
               fix="add one covering the generated folders this project produces")
        return
    text = read_bytes(path).decode("utf-8", "replace")

    files = git_tracked(ctx.root)
    if files is None:
        files = walk_files(ctx.root)
    kinds = project_kinds(files)

    needed = []
    if "python" in kinds:
        needed += [p for p in ("__pycache__", "*.pyc") if p not in text]
    if "node" in kinds:
        needed += [p for p in ("node_modules",) if p not in text]

    # A junk folder that exists on disk and is not covered is one `git add .`
    # away from being committed. A pattern for an absent folder is noise.
    ignore = load_gitignore(ctx.root)
    for base, dirs, _ in os.walk(ctx.root):
        dirs[:] = [d for d in dirs if d != ".git"]
        for d in list(dirs):
            if d not in JUNK_DIRS:
                continue
            rel = os.path.relpath(os.path.join(base, d), ctx.root)
            dirs.remove(d)
            if d not in text and not covered_by_gitignore(rel, ignore) and d not in needed:
                needed.append(d)

    if needed:
        r.info("gitignore.incomplete",
               "missing common patterns: %s" % ", ".join(needed),
               fix="append them so build output stops being committed")


def check_license(ctx):
    r = ctx.report
    names = ["LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "LICENCE"]
    path = None
    for n in names:
        p = os.path.join(ctx.root, n)
        if os.path.isfile(p):
            path = p
            break
    if not path:
        r.warn("license.missing", "no LICENSE file",
               fix="add one; a repo without a licence is not usable by anyone")
        return None
    spdx = detect_license(read_bytes(path).decode("utf-8", "replace"))
    if not spdx:
        r.warn("license.unrecognised", "LICENSE file does not match a known licence",
               path=os.path.basename(path), fix="use an SPDX template")
        return None
    r.info("license.detected", "LICENSE file is %s" % spdx)

    readme = None
    for n in ("README.md", "README.markdown", "README.rst", "README.txt"):
        p = os.path.join(ctx.root, n)
        if os.path.isfile(p):
            readme = read_bytes(p).decode("utf-8", "replace")
            break
    if readme:
        mentioned = license_mentions(readme)
        others = [m for m in mentioned if m != spdx and m in LICENSE_ALIASES]
        if spdx in mentioned:
            # The README names the file's licence, so other names are references
            # (dependencies, a per-repository note), not a claim about this repo.
            if others:
                r.info("license.other-mentions",
                       "README also names %s alongside %s; read as a reference"
                       % (", ".join(sorted(set(others))), spdx))
        elif others:
            r.error("license.mismatch",
                    "LICENSE file is %s but README says %s"
                    % (spdx, ", ".join(sorted(set(others)))),
                    path="README",
                    fix="make the README match the file; the file is authoritative")
    return spdx


def check_workflows(ctx):
    r = ctx.report
    wf_dir = os.path.join(ctx.root, ".github", "workflows")
    if not os.path.isdir(wf_dir):
        r.info("ci.none", "no GitHub Actions workflow",
               fix="optional, but a green badge is the cheapest trust signal")
        return
    files = [f for f in sorted(os.listdir(wf_dir)) if f.endswith((".yml", ".yaml"))]
    if not files:
        r.info("ci.none", "workflows folder exists but holds no .yml")
        return

    today = datetime.date.today()
    for f in files:
        rel = os.path.join(".github", "workflows", f)
        text = read_bytes(os.path.join(wf_dir, f)).decode("utf-8", "replace")

        for m in re.finditer(r"node-version:\s*\[([^\]]*)\]", text):
            for part in m.group(1).split(","):
                v = part.strip().strip("'\"")
                if not v.isdigit():
                    continue
                major = int(v)
                eol = NODE_EOL.get(major)
                if eol and datetime.date.fromisoformat(eol) < today:
                    r.warn("ci.eol-node",
                           "matrix pins Node %s, which reached end of life on %s"
                           % (major, eol), path=rel,
                           fix="drop it from the matrix; keep supported majors only")

        for m in re.finditer(r"uses:\s*([^\s#]+)", text):
            ref = m.group(1)
            if "@" not in ref:
                r.warn("ci.unpinned-action", "action has no version: %s" % ref,
                       path=rel, fix="pin to a tag or a commit SHA")
            elif ref.split("@", 1)[1] in ("master", "main"):
                r.warn("ci.floating-action", "action tracks a moving branch: %s" % ref,
                       path=rel, fix="pin to a released tag")

        if "matrix" in text and "fail-fast: false" in text:
            r.info("ci.no-fail-fast",
                   "matrix with fail-fast: false keeps every other job running "
                   "after the first failure", path=rel)


# --------------------------------------------------------------------------
# remote checks
# --------------------------------------------------------------------------

def check_meta(ctx):
    r = ctx.report
    if not ctx.gh or not ctx.owner:
        r.info("meta.skipped", "no gh CLI or no owner; metadata checks skipped")
        return
    meta = ctx.api("")
    if not meta:
        r.error("meta.unreachable", "cannot read repo metadata for %s" % ctx.slug,
                fix="check `gh auth status`")
        return

    desc = (meta.get("description") or "").strip()
    if not desc:
        r.error("meta.description", "repository has no description",
                fix="say what it does, on which platform, in one line")
    elif len(desc) < 20:
        r.warn("meta.description", "description is %d chars; too short to be found"
               % len(desc), fix="add what it does and the platform")
    else:
        low = desc.lower()
        hits = [w for w in FILLER if w in low]
        if len(hits) >= 2:
            r.warn("meta.filler",
                   "description is mostly adjectives: %s" % ", ".join(hits),
                   fix="replace them with what the thing actually does")

    home = (meta.get("homepage") or "").strip()
    if not home:
        if is_profile_repo(ctx):
            # The profile README repo has no releases and no package; a homepage
            # there points nowhere, so the warning would be pure noise.
            r.info("meta.homepage-skipped", "profile README repo; homepage not applicable")
        else:
            r.warn("meta.homepage", "no homepage set",
                   fix="point it at releases/latest, or at the package page if published")

    topics = meta.get("topics") or []
    if not topics:
        r.warn("meta.topics", "no topics",
               fix="add 5-10 lowercase topics; they are the search surface")
    for problem in validate_topics(topics):
        level = "error" if "topic over" in problem or "max 20" in problem else "warn"
        r.add(level, "meta.topics", problem,
              fix="lowercase letters, digits and hyphens; max 20 topics, 50 chars each")

    if meta.get("archived"):
        r.info("meta.archived", "repository is archived")

    api_license = ((meta.get("license") or {}).get("spdx_id") or "").strip()
    local = None
    for n in ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "LICENCE"):
        p = os.path.join(ctx.root, n)
        if os.path.isfile(p):
            local = detect_license(read_bytes(p).decode("utf-8", "replace"))
            break
    if api_license and local and api_license != "NOASSERTION" and api_license != local:
        r.warn("license.api-drift",
               "GitHub detects %s but the LICENSE file reads %s"
               % (api_license, local),
               fix="re-upload the exact licence text so the sidebar agrees")


def check_releases(ctx):
    r = ctx.report
    if not ctx.gh or not ctx.owner:
        return
    rels = ctx.api("/releases?per_page=10")
    if rels is None:
        r.warn("release.unreachable", "cannot list releases for %s" % ctx.slug)
        return
    if not rels:
        r.info("release.none", "no releases")
        return

    latest = rels[0]
    if latest.get("draft"):
        r.warn("release.draft", "the newest release is a draft: %s"
               % latest.get("tag_name"))
    if latest.get("prerelease"):
        r.info("release.prerelease", "latest release is a prerelease: %s"
               % latest.get("tag_name"))
    if not latest.get("assets"):
        r.warn("release.no-assets", "latest release has no assets: %s"
               % latest.get("tag_name"))

    for rel in rels:
        tag = rel.get("tag_name") or ""
        if tag and not re.search(r"\d", tag):
            r.warn("release.unversioned-tag",
                   "tag carries no version: %s" % tag,
                   fix="use vMAJOR.MINOR.PATCH so links and badges stay stable")

    tags = ctx.api("/tags?per_page=30") or []
    if len(tags) > len(rels):
        missing = [t["name"] for t in tags[:5]
                   if t["name"] not in {x.get("tag_name") for x in rels}]
        if missing:
            r.info("release.tag-without-release",
                   "tags without a release page: %s" % ", ".join(missing[:5]))


def check_download_status(ctx, text):
    """HEAD every releases/latest/download link in the README."""
    if not text or not ctx.owner:
        return
    r = ctx.report
    links = sorted({x.rstrip(".,;:") for x in DOWNLOAD_RE.findall(strip_code(text))})
    for name in links:
        url = "https://github.com/%s/%s/releases/latest/download/%s" % (
            ctx.owner, ctx.name, name)
        status = head(url)
        if status in (200, 206):
            r.info("download.ok", "%s -> HTTP %s" % (name, status))
        else:
            r.error("download.broken", "%s -> HTTP %s" % (name, status or "no response"),
                    fix="the link does not resolve; rename the asset or fix the URL")


def check_external_links(ctx, text):
    if not text or not ctx.check_links:
        return
    r = ctx.report
    urls = []
    placeholders = []
    for u in HTTP_RE.findall(strip_code(text)):
        u = u.rstrip(".,;:)")
        if u in urls or u in placeholders:
            continue
        if is_placeholder_url(u):
            placeholders.append(u)
        else:
            urls.append(u)
    for u in placeholders:
        r.info("links.placeholder", "example URL, not requested: %s" % u)
    if len(urls) > ctx.max_links:
        r.info("links.truncated", "checking the first %d of %d links"
               % (ctx.max_links, len(urls)))
        urls = urls[:ctx.max_links]

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        future_map = {pool.submit(head, u): u for u in urls}
        for fut in concurrent.futures.as_completed(future_map):
            results[future_map[fut]] = fut.result()

    for url, status in sorted(results.items()):
        if status and status < 400:
            continue
        host_blocked = any(h in url for h in BOT_BLOCKING_HOSTS)
        if status in (403, 429) and host_blocked:
            r.info("links.bot-blocked",
                   "HTTP %s (expected for bots): %s" % (status, url))
        elif status == 0:
            r.warn("links.unreachable", "no response: %s" % url)
        else:
            r.error("links.dead", "HTTP %s: %s" % (status, url),
                    fix="fix or remove the link")


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def audit(ctx):
    text = check_readme(ctx)
    check_download_links(ctx, text)
    check_encoding(ctx)
    check_tree(ctx)
    check_gitignore(ctx)
    check_license(ctx)
    check_workflows(ctx)
    check_meta(ctx)
    check_releases(ctx)
    check_download_status(ctx, text)
    check_external_links(ctx, text)
    return ctx.report


def print_report(slug, report, verbose):
    order = {"error": 0, "warn": 1, "info": 2}
    rows = [r for r in report.rows if verbose or r["level"] != "info"]
    rows.sort(key=lambda r: (order[r["level"]], r["check"], r.get("path") or ""))

    print("")
    print("repo-polish  %s" % slug)
    print("-" * 66)
    if not rows:
        print("  clean")
    for row in rows:
        mark = {"error": "FAIL", "warn": "WARN", "info": "info"}[row["level"]]
        where = " (%s)" % row["path"] if row.get("path") else ""
        print("  %-4s %-22s %s%s" % (mark, row["check"], row["message"], where))
        if row.get("fix") and row["level"] != "info":
            print("       -> %s" % row["fix"])
    print("-" * 66)
    print("  %d error(s), %d warning(s), %d note(s)"
          % (report.count("error"), report.count("warn"), report.count("info")))


def cmd_audit(args):
    targets = []  # (root, owner, name)
    if args.all:
        gh_bin = find_gh()
        if not gh_bin:
            print("repo-polish: --all needs the gh CLI", file=sys.stderr)
            return EXIT_ERROR
        gh = Gh(gh_bin)
        user = gh.json("user")
        if not user:
            print("repo-polish: `gh auth status` is not logged in", file=sys.stderr)
            return EXIT_ERROR
        owner = args.owner or user["login"]
        repos = gh.json("users/%s/repos?per_page=100&sort=updated" % owner) or []
        for r in repos:
            if r.get("fork"):
                continue
            targets.append((os.getcwd(), owner, r["name"]))
    else:
        for target in args.targets or ["."]:
            root = os.path.abspath(target)
            if not os.path.isdir(root):
                print("repo-polish: not a folder: %s" % target, file=sys.stderr)
                return EXIT_ERROR
            owner, name = repo_identity(root)
            targets.append((root, args.owner or owner, name))

    all_reports = []
    exit_code = EXIT_CLEAN
    threshold = SEVERITY[args.fail_on]
    for root, owner, name in targets:
        ctx = Ctx(root, owner=owner, name=name, offline=args.offline,
                  check_links=args.check_links, max_links=args.max_links)
        report = audit(ctx)
        all_reports.append((ctx.slug, report))
        if not args.json:
            print_report(ctx.slug, report, args.verbose)
        if any(SEVERITY[r["level"]] >= threshold for r in report.rows):
            exit_code = EXIT_ISSUES

    if args.json:
        payload = [{"repo": slug, "findings": rep.rows,
                    "errors": rep.count("error"), "warnings": rep.count("warn")}
                   for slug, rep in all_reports]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return exit_code


def cmd_bom(args):
    changed = 0
    for target in args.targets or ["."]:
        for path in tracked_files(os.path.abspath(target)):
            if os.path.splitext(path)[1].lower() not in TEXT_EXTS:
                continue
            raw = read_bytes(path)
            if not raw.startswith(b"\xef\xbb\xbf"):
                continue
            if args.fix:
                with open(path, "wb") as fh:
                    fh.write(raw[3:])
                print("stripped BOM  %s" % os.path.relpath(path, target))
            else:
                print("BOM           %s" % os.path.relpath(path, target))
            changed += 1
    if not changed:
        print("no BOM found")
        return EXIT_CLEAN
    return EXIT_CLEAN if args.fix else EXIT_ISSUES


def cmd_meta(args):
    # Validate the spec before touching the network, so a bad topic list fails
    # in milliseconds and never half-writes metadata.
    spec = None
    if args.apply:
        try:
            with open(args.apply, "r", encoding="utf-8") as fh:
                spec = json.load(fh)
        except (OSError, ValueError) as exc:
            print("repo-polish: cannot read %s: %s" % (args.apply, exc), file=sys.stderr)
            return EXIT_ERROR
        problems = validate_topics(spec.get("topics") or [])
        if problems:
            for p in problems:
                print("repo-polish: %s" % p, file=sys.stderr)
            return EXIT_ISSUES

    gh_bin = find_gh()
    if not gh_bin:
        print("repo-polish: meta needs the gh CLI", file=sys.stderr)
        return EXIT_ERROR
    gh = Gh(gh_bin)

    if args.show or not args.apply:
        owner, name = repo_identity(os.path.abspath(args.targets[0] if args.targets else "."))
        if args.owner:
            owner = args.owner
        if not owner:
            print("repo-polish: cannot tell which repo this is; pass --owner",
                  file=sys.stderr)
            return EXIT_ERROR
        meta = gh.json("repos/%s/%s" % (owner, name))
        if not meta:
            print("repo-polish: cannot read %s/%s" % (owner, name), file=sys.stderr)
            return EXIT_ERROR
        print(json.dumps({
            "description": meta.get("description"),
            "homepage": meta.get("homepage"),
            "topics": meta.get("topics"),
        }, indent=2, ensure_ascii=False))
        return EXIT_CLEAN

    slug = spec.get("repo") or args.repo
    if not slug and args.targets:
        owner, name = repo_identity(os.path.abspath(args.targets[0]))
        if owner:
            slug = "%s/%s" % (owner, name)
    if not slug:
        print("repo-polish: spec needs a \"repo\": \"owner/name\" field",
              file=sys.stderr)
        return EXIT_ERROR

    topics = spec.get("topics") or []

    patch = {}
    for key in ("description", "homepage"):
        if key in spec:
            patch[key] = spec[key]
    if patch:
        rc, out, err = gh.call(["repos/%s" % slug], payload=patch)
        if rc != 0:
            print("repo-polish: PATCH failed: %s" % err.strip(), file=sys.stderr)
            return EXIT_ERROR
        print("updated  %s -> %s" % (slug, ", ".join(sorted(patch))))
    if "topics" in spec:
        rc, out, err = gh.call(["repos/%s/topics" % slug],
                               payload={"names": topics}, method="PUT")
        if rc != 0:
            print("repo-polish: PUT topics failed: %s" % err.strip(), file=sys.stderr)
            return EXIT_ERROR
        print("updated  %s -> %d topics" % (slug, len(topics)))
    return EXIT_CLEAN


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="repo-polish",
        description="Audit a GitHub repository for the defects that make it look abandoned.")
    parser.add_argument("--version", action="version", version="repo-polish " + VERSION)
    sub = parser.add_subparsers(dest="command")

    a = sub.add_parser("audit", help="read-only audit (default)")
    a.add_argument("targets", nargs="*", help="repo folders (default: .)")
    a.add_argument("--all", action="store_true", help="every non-fork repo of the user")
    a.add_argument("--owner", help="GitHub owner, when the remote cannot be read")
    a.add_argument("--json", action="store_true", help="machine-readable output")
    a.add_argument("--verbose", action="store_true", help="include notes")
    a.add_argument("--offline", action="store_true", help="skip every network check")
    a.add_argument("--check-links", action="store_true", help="HTTP-check external links")
    a.add_argument("--max-links", type=int, default=200)
    a.add_argument("--fail-on", choices=("error", "warn", "info"), default="error",
                   help="exit 1 when a finding at this level or worse exists")

    b = sub.add_parser("bom", help="find or strip UTF-8 BOM")
    b.add_argument("targets", nargs="*", help="repo folders (default: .)")
    b.add_argument("--fix", action="store_true", help="rewrite the files without the BOM")

    m = sub.add_parser("meta", help="read or write description/homepage/topics")
    m.add_argument("targets", nargs="*", help="repo folder")
    m.add_argument("--owner")
    m.add_argument("--repo", help="owner/name, when not reading a local remote")
    m.add_argument("--show", action="store_true", help="print current metadata")
    m.add_argument("--apply", metavar="FILE", help="JSON spec to write")

    args = parser.parse_args(argv)
    if not args.command:
        args = parser.parse_args(["audit"] + list(argv or []))

    try:
        if args.command == "audit":
            return cmd_audit(args)
        if args.command == "bom":
            return cmd_bom(args)
        if args.command == "meta":
            return cmd_meta(args)
    except KeyboardInterrupt:
        return EXIT_ERROR
    return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
