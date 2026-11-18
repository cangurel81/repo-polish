---
name: repo-polish
description: Audit and fix a GitHub repository so it reads as maintained instead of abandoned. Catches missing or broken README download links, dead `releases/latest/download/<asset>` URLs, UTF-8 BOM and mojibake, tracked build junk, missing or mismatched LICENSE, end-of-life Node versions in CI, unpinned actions, and empty or keyword-stuffed description, homepage and topics. Runs offline with no dependencies beyond Python 3.8 standard library; uses the `gh` CLI only for remote checks and for writing metadata. Use when the user says a repo looks abandoned, asks why nobody stars or installs it, wants a repo audit or polish pass, needs a download link that actually works, wants description/homepage/topics written, or asks to check licensing, encoding, links or release assets across several repositories.
---

# repo-polish

Find the small defects that make a working project look dead, then fix them in
the order that costs the least.

A repository is judged in about eight seconds: is there a README that says what
this is, is there a download that works, is there a licence, does the last
commit look like this year. Everything in this skill targets one of those
signals.

## When to use

- "My repo gets no stars / no downloads / no contributors."
- Before announcing a release or posting the repo anywhere.
- After renaming a repo, moving an asset, or changing a licence.
- Sweeping every repo on an account before a portfolio or job application.
- The user asks whether the README, licence, links, topics or CI are right.

## Quick start

```bash
# audit the repo in the current folder
python scripts/repo_polish.py audit .

# audit a folder that is not the cwd
python scripts/repo_polish.py audit /path/to/repo --verbose

# no network at all (README, encoding, tree, licence, CI only)
python scripts/repo_polish.py audit . --offline

# every non-fork repo of the logged-in gh user
python scripts/repo_polish.py audit --all --verbose

# machine-readable, for a dashboard or a loop
python scripts/repo_polish.py audit . --json

# also HTTP-check every external link in the README
python scripts/repo_polish.py audit . --check-links
```

Fixing:

```bash
python scripts/repo_polish.py bom . --fix          # strip UTF-8 BOM in place
python scripts/repo_polish.py meta . --show        # print live description/homepage/topics
python scripts/repo_polish.py meta . --apply spec.json   # write them
```

`spec.json`:

```json
{
  "repo": "owner/name",
  "description": "Wake-on-LAN client for Windows and Android, built with Expo.",
  "homepage": "https://github.com/owner/name/releases/latest",
  "topics": ["wake-on-lan", "wol", "expo", "windows", "android"]
}
```

Topics are validated before anything is sent: max 20, max 50 characters each,
lowercase letters, digits and hyphens only, no duplicates. A bad list exits 1
without touching the network.

## What it checks

| Check | Severity | What it means |
|---|---|---|
| `readme.present` | error | No README at the root |
| `readme.thin` | warn | README under 200 characters |
| `readme.bom` | error | README starts with a UTF-8 BOM |
| `readme.relative-link` | error | A relative link or image points at a missing file |
| `readme.placeholder` | warn | `TODO`, lorem ipsum, `your-username` left in |
| `readme.no-download-link` | warn | The latest release has assets but the README never links them |
| `readme.dead-download` | error | README links an asset the latest release does not have |
| `readme.versioned-asset` | warn | Download URL contains a version, so it 404s on the next release |
| `download.broken` | error | `releases/latest/download/<asset>` does not return 200/206 |
| `encoding.bom` | error | UTF-8 BOM in a tracked text file |
| `encoding.not-utf8` | error | File is not valid UTF-8 |
| `encoding.mojibake` | error | cp1254/cp437 damage such as `Ã¼` or `├╝` |
| `tree.junk-dir` | error | `__pycache__`, `node_modules`, `dist`, `build`, `.venv` tracked |
| `tree.binary` | warn | `.exe`, `.zip`, `.dll`, `.pyc` tracked; ship it as a release asset |
| `gitignore.missing` | warn | No `.gitignore` |
| `gitignore.incomplete` | note | Common patterns absent |
| `license.missing` | warn | No LICENSE file |
| `license.unrecognised` | warn | LICENSE body matches no known licence |
| `license.mismatch` | error | LICENSE file and README name different licences |
| `license.api-drift` | warn | GitHub's detected licence differs from the file |
| `ci.none` | note | No workflow |
| `ci.eol-node` | warn | Matrix pins a Node major past its end-of-life date |
| `ci.unpinned-action` | warn | `uses:` with no `@version` |
| `ci.floating-action` | warn | `uses:` tracks `main`/`master` |
| `meta.description` | error/warn | Missing, too short, or mostly adjectives |
| `meta.filler` | warn | Two or more filler adjectives in the description |
| `meta.homepage` | warn | Homepage empty |
| `meta.topics` | warn/error | Missing, too many, or invalid topics |
| `release.no-assets` | warn | Latest release ships nothing |
| `release.unversioned-tag` | warn | Tag carries no version number |
| `release.tag-without-release` | note | Tags exist with no release page |
| `links.dead` | error | External link returns 4xx/5xx (with `--check-links`) |
| `links.placeholder` | note | Example host, not requested (`example.com`, `localhost`) |
| `links.bot-blocked` | note | 403/429 from a host known to block bots |

## Reading the output

```
  FAIL readme.dead-download     README links download/app-v1.2.0.exe but the latest
                                release has: app.exe
       -> rename the link to an existing asset
```

Each finding prints a level, a stable check id, a sentence, and a fix. The check
id is what you grep for and what the docs describe. `--verbose` adds notes
(`info`), which are context rather than problems.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | clean at the chosen threshold |
| 1 | findings at or above `--fail-on` (default `error`) |
| 2 | tool error: bad path, no `gh`, unreadable spec |

`--fail-on warn` turns the exit code into a useful CI gate. `--fail-on info`
is only useful for producing a full report.

## Fix order

Cheapest and highest impact first. Each step is verifiable.

1. **Download link.** If the README says "Download" and the link 404s, nothing
   else matters. Upload the asset without a version in the file name, then link
   `releases/latest/download/<asset>`. A versioned name guarantees the next
   release breaks the link.
2. **Description and topics.** One line: what it does, on which platform. No
   "powerful", "seamless", "next-generation". Topics are the search surface —
   5 to 10 lowercase, hyphenated.
3. **Licence.** Pick one, put the exact SPDX text in `LICENSE`, and make the
   README say the same thing. A repo without a licence cannot be used by anyone,
   so nobody will.
4. **README skeleton.** What it is, screenshot or demo, install, usage, exit
   codes, licence. Under 200 characters is a warning, not a style preference.
5. **Encoding.** `bom . --fix`, then retype mojibake by hand — never try to
   transcode it back, the bytes are already lost.
6. **Tree.** `git rm -r --cached dist/ build/ __pycache__/`, add them to
   `.gitignore`, ship binaries as release assets.
7. **CI.** Keep supported Node majors only, pin actions to a released tag.
   A red badge is worse than no badge.

## Pitfalls

- **`bin/` is not build output.** It holds source in most Node and Python CLIs.
  The tool does not flag it, and neither should you.
- **Single box-drawing characters are not mojibake.** `├ ─ └` are legitimate
  tree art. Only multi-character sequences like `├╝` or `Ã¼` are damage.
- **`tree.*` follows git, not the disk.** With git available only `git ls-files`
  output is judged, so a `__pycache__` folder that `.gitignore` already covers
  is not a finding. Without git the disk is walked and `.gitignore` applied by
  hand — same question, different source.
- **`.repo-polish-ignore` silences encoding checks only.** One glob per line,
  `#` for comments. Documentation that has to quote `Ã¼` or `├╝` to explain the
  check would otherwise fail the repository's own audit.
- **Do not pipe `gh` through PowerShell `>`.** On a Turkish Windows console that
  writes UTF-16 with cp1254 damage. Write JSON from Python as UTF-8 and pass it
  with `gh api --input`, or use `meta --apply`, which does exactly that.
- **`gh release upload --clobber` resets the download counter.** Re-uploading a
  fixed asset costs the visible number.
- **A 403 is not always a dead link.** npm, PyPI, LinkedIn, X and the browser
  extension stores block automated clients. The tool reports those as notes.
- **Links inside code are not links.** A README that documents a URL shape, or
  quotes a broken example, writes it in a fenced block or a code span. Those are
  skipped, and so are example hosts (`example.com`, `localhost`) — they are
  reported as `links.placeholder` notes instead of being requested.
- **GitHub's licence detection lags.** `license.api-drift` usually means the
  file was replaced without the sidebar re-reading it; push the exact SPDX text.
- **`--all` audits the current working directory against each remote repo.**
  It is a metadata sweep, not a clone. Run it per checkout when you need the
  file-level checks.

## Limits

- README parsing is regex-based. Unusual markup (MDX, custom components) may
  produce false positives; the finding names the exact link so it is easy to
  dismiss.
- `--check-links` is sequential-ish (8 threads) and caps at `--max-links`
  (default 200). A large README with hundreds of links will be truncated with a
  note.
- Node end-of-life dates live in a table in the script. Refresh it once a year;
  the table is checked against `date.today()`, so stale entries produce false
  warnings rather than silence.
- The tool does not judge writing quality, screenshots, or whether the project
  is actually useful. It only removes the reasons a visitor leaves early.
