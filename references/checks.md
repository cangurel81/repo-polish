# Check reference

Every finding has a stable id. This page explains what each one catches, why it
costs you visitors, and how to fix it. Ids are grouped by area; the prefix is
the part before the dot.

Severity levels: **error** means the repository is broken for a visitor,
**warn** means it looks neglected, **note** is context rather than a problem.

---

## readme.*

### `readme.present` (error)

No README at the repository root. This is the single worst signal: a visitor
cannot tell what the project is, so they leave. Nothing else on this page
matters until it exists.

**Fix:** add `README.md` with what it is, a screenshot or demo, install, usage,
and licence.

### `readme.thin` (warn)

The README is under 200 characters. That is a title and a sentence, which reads
as "uploaded once and forgotten".

**Fix:** the four headings in `readme.present` above are enough to clear it.

### `readme.bom` (error)

The README starts with a UTF-8 BOM (`EF BB BF`). GitHub renders the BOM as an
invisible character, but tooling that reads the file sees it, and on Windows it
is the usual cause of a mangled first heading.

**Fix:** `python scripts/repo_polish.py bom . --fix`.

### `readme.relative-link` (error)

A relative link or image points at a file that does not exist — `docs/guide.md`,
`assets/demo.gif`, `CONTRIBUTING.md`. The visitor clicks and gets a 404 page.

Percent-escaped spaces (`docs/my%20file.md`) are decoded before the check, so a
correct link with a space in the name does not false-positive.

**Fix:** create the file, or correct the path. Case matters on Linux and in
GitHub's file view even when it does not on your machine.

### `readme.placeholder` (warn)

Leftover scaffolding: `TODO`, `lorem ipsum`, `your-username`, `example.com`
links in a download section. It reads as a template someone never finished.

**Fix:** replace with real values or delete the section.

### `readme.no-download-link` (warn)

The latest release ships at least one asset, and the README never links it. This
is the most expensive finding in the list: you built the thing, published it,
and nobody can find it. Users do not browse the Releases tab; they read the
README.

**Fix:** add a Download section that points at
`https://github.com/<owner>/<repo>/releases/latest/download/<asset>`.

### `readme.dead-download` (error)

The README links a `releases/latest/download/<name>` URL whose asset is not in
the latest release. The link 404s for every visitor, and it is the one link they
actually wanted.

**Fix:** rename the link to an asset that exists, or upload the missing asset.

### `readme.versioned-asset` (warn)

The download URL contains a version number, e.g. `app-v1.2.0.exe` under
`releases/latest/download/`. It works today and breaks the moment you publish
the next release, because `latest` moves and the file name does not.

**Fix:** upload assets with a version-free name (`app.exe`) and let the tag carry
the version. If the name must contain a version, link the release page instead.

---

## download.*

### `download.broken` (error)

An actual HTTP request to every `releases/latest/download/<asset>` URL in the
README did not return 200 or 206. This catches the cases static analysis cannot:
a private repository, a deleted asset, a renamed release.

**Fix:** upload the asset, or correct the URL. Requires `gh` for the asset list
and a network connection; skipped with `--offline`.

---

## encoding.*

### `encoding.bom` (error)

A UTF-8 BOM at the start of a tracked text file. It breaks shebangs, JSON
parsers, PHP headers and some Windows tooling, and it is invisible in most
editors.

**Fix:** `bom . --fix`. Verify with `bom .` afterwards; it should print
`no BOM found`.

### `encoding.not-utf8` (error)

The file is not valid UTF-8 — usually cp1254 (Turkish), cp1252 (Western) or
cp1251 saved from an old editor. GitHub shows replacement characters where the
non-ASCII text should be.

**Fix:** re-save the file as UTF-8 without BOM. Do not "convert" it with a tool
that guesses the source encoding; open it, confirm the text reads correctly, and
save.

### `encoding.mojibake` (error)

Multi-character damage such as `Ã¼` (UTF-8 read as cp1254) or `├╝` (UTF-8 read as
cp437). The bytes are already wrong, so transcoding back does not recover them.

**Fix:** retype the affected words. This check only looks for multi-character
sequences, because single box-drawing characters (`├ ─ └`) are legitimate tree
art and flagging them produced a false positive on every README with a directory
tree.

Files listed in `.repo-polish-ignore` are skipped, so documentation that quotes
damaged characters on purpose still passes.

---

## tree.*

### `tree.junk-dir` (error)

A generated directory is tracked: `__pycache__`, `node_modules`, `dist`,
`build`, `.venv`, `.pytest_cache`, `target`, `.next`. It bloats the clone,
creates merge conflicts nobody can resolve, and makes the repo look like a
folder someone zipped and pushed.

`bin/` is deliberately **not** in this list: it holds source in most Node and
Python CLIs.

**Fix:**

```bash
git rm -r --cached dist/ build/ __pycache__/
echo "dist/" >> .gitignore
```

### `tree.binary` (warn)

A binary or build artifact is tracked: `.exe`, `.dll`, `.zip`, `.pyc`, `.so`,
`.dylib`. Binaries in the tree grow the clone forever; git cannot delta them.

**Fix:** attach them to a release instead, and add the pattern to `.gitignore`.

---

## gitignore.*

### `gitignore.missing` (warn)

No `.gitignore`. Every editor artefact, build folder and local env file is one
`git add .` away from being committed.

**Fix:** add one. Even five lines (`__pycache__/`, `node_modules/`, `dist/`,
`.venv/`, `.DS_Store`) is enough.

### `gitignore.incomplete` (note)

The `.gitignore` exists but is missing patterns that are standard for the
project's language. A note, not a problem.

**Fix:** append the missing lines.

---

## license.*

### `license.missing` (warn)

No LICENSE file. Without a licence nobody may legally use, copy or contribute to
the code, so a cautious developer closes the tab. This is the second most
expensive finding after a dead download link.

**Fix:** pick one and paste the exact text — MIT for small tools, GPL-3.0 or
AGPL-3.0 if you want derivatives to stay open, Apache-2.0 if you need a patent
grant.

### `license.unrecognised` (warn)

A LICENSE file exists but its body matches no known licence — a custom text, a
truncated paste, or a "All rights reserved" notice. GitHub cannot show a licence
badge for it, and some companies will not touch it.

**Fix:** replace with the exact SPDX text.

### `license.mismatch` (error)

The LICENSE file says one thing and the README says another. This is worse than
having no licence, because a reader cannot tell which one applies.

The check only fires when the README never names the licence the file carries. A
README that says "MIT for most, GPL-3.0 where noted" has named MIT, so the GPL
mention is read as a reference to other repositories, not a claim about this one.

**Fix:** decide, then make both say the same thing.

### `license.other-mentions` (note)

The README names the file's licence and at least one other. Usually a monorepo or
profile README describing several projects, or a compatibility note. Nothing to
do unless the other name is a mistake.

### `license.api-drift` (warn)

GitHub's own licence detection disagrees with the file. Usually the file was
replaced without the sidebar re-reading it, or the text is a modified copy.

**Fix:** push the exact SPDX text; the badge updates on the next index.

### `license.detected` (note)

Informational: the licence the file was recognised as. Useful when the answer
surprises you.

---

## ci.*

### `ci.none` (note)

No GitHub Actions workflow. Fine for a small tool; a problem for anything people
are expected to trust.

### `ci.eol-node` (warn)

A workflow matrix pins a Node major version that is past its end-of-life date.
The job may still pass today, but it tests a runtime nobody should deploy on,
and the badge will go red when the runner image drops it.

Dates live in a table in the script and are compared against today's date, so a
stale table produces false warnings rather than silence.

**Fix:** drop the EOL major from the matrix, keep two or three supported ones.

### `ci.unpinned-action` (warn)

`uses: actions/checkout` with no `@version`. The workflow resolves whatever is
current, so a breaking upstream change turns your badge red without a commit
from you.

**Fix:** `uses: actions/checkout@v4`.

### `ci.floating-action` (warn)

`uses: some/action@main` or `@master`. You are running whatever the author
pushed five minutes ago, including a compromised release.

**Fix:** pin to a tag or a commit SHA.

### `ci.no-fail-fast` (note)

The matrix sets `fail-fast: false`, so one failing job does not cancel the rest.
That is usually what you want — you see every broken combination in one run.

---

## meta.*

These need `gh` and a logged-in account. Without either, `meta.skipped` is
reported instead and the rest of the audit still runs.

### `meta.description` (error/warn)

Missing, or shorter than ~25 characters. The description is the one line shown
in search results, on your profile, and in every repo list.

**Fix:** what it does plus the platform. "Wake-on-LAN client for Windows and
Android, built with Expo." Not "A powerful, modern, seamless tool."

### `meta.filler` (warn)

Two or more filler adjectives in the description: powerful, seamless, modern,
next-generation, blazing, robust, cutting-edge, innovative. They carry no
information and read as generated.

**Fix:** delete them; keep the nouns.

### `meta.homepage` (warn)

No homepage set. For a project with releases this should point at
`releases/latest`, which is the download page. For a published package, the
registry page.

Skipped for the `<owner>/<owner>` profile repo: it has no releases and no package,
so any homepage there points nowhere.

### `meta.topics` (warn/error)

Missing, more than 20, or invalid. Topics are the only search surface GitHub
gives a small repo; without them the project is invisible to `github.com/topics`.

Rules: max 20, max 50 characters each, lowercase letters, digits and hyphens
only, no duplicates.

**Fix:** `meta . --apply spec.json`, which validates before writing.

### `meta.archived` (note)

The repository is archived, so read-only. Expected for retired projects;
surprising otherwise.

### `meta.unreachable` (error)

The API call for repository metadata failed. Usually a typo in the owner/name,
or the token lost the `repo` scope.

---

## release.*

### `release.none` (note)

No releases at all. For a CLI or desktop app this means there is nothing to
download.

### `release.draft` (warn)

The newest release is a draft, so nobody but you can see it.

### `release.prerelease` (note)

The latest release is a prerelease. Fine, but `releases/latest` skips it, so any
`releases/latest/download/...` link will 404.

### `release.no-assets` (warn)

The release has no assets. A release page with only source archives is not a
download for anyone who does not want to build it.

**Fix:** build and attach the binary.

### `release.unversioned-tag` (warn)

The tag carries no version number (`first`, `latest`, `release`). Two tags with
the same meaning cannot be ordered, and `latest` already means something else on
GitHub.

**Fix:** use `v1.0.0`, and keep the old tag if links point at it.

### `release.tag-without-release` (note)

Tags exist with no release page. Harmless, but a visitor who lands on the tag
list sees no download.

---

## links.*

Only with `--check-links`. Every external link in the README is requested; the
first `--max-links` (default 200) are checked, then `links.truncated` notes the
rest.

### `links.dead` (error)

The link returns 4xx or 5xx and is not a known bot-blocking host. A dead link in
a README costs trust faster than a missing one.

**Fix:** update or remove it.

### `links.bot-blocked` (note)

403 or 429 from npm, PyPI, LinkedIn, X, the browser extension stores and similar
hosts that refuse automated clients. The link is probably fine in a browser.

### `links.unreachable` (warn)

No response at all: DNS failure, timeout, TLS error. Usually a dead domain, but
occasionally a firewall on your side.
