# repo-polish

Audit and fix a GitHub repository so it reads as maintained instead of abandoned.

A visitor decides in about eight seconds: is there a README that says what this
is, is there a download that works, is there a licence, does the last commit look
like this year. `repo-polish` checks exactly those signals, names each finding
with a stable id, and prints the fix next to it.

- **No dependencies.** Python 3.8+ standard library only.
- **Works offline.** `--offline` runs every file-level check without a network.
- **`gh` is optional.** Missing `gh` turns remote checks into a note, not a crash.
- **Scriptable.** `--json` for dashboards, `--fail-on` for CI.

```
$ python scripts/repo_polish.py audit . --verbose

repo-polish  cangurel81/skill-router
------------------------------------------------------------------
  WARN readme.no-download-link latest release ships 1 asset(s) but the README never links them
       -> add a Download section pointing at releases/latest/download/<asset>
  info ci.no-fail-fast        matrix with fail-fast: false keeps every other job running after the first failure
  info license.detected       LICENSE file is MIT
------------------------------------------------------------------
  0 error(s), 1 warning(s), 2 note(s)
```

That finding was real: the release shipped `skill-router-v1.0.1.zip` and the
README linked only the releases page.

## Install

Three ways, pick one.

### 1. As a skill (Claude Code and OpenCode)

Copy the folder into your skills directory:

```bash
# Claude Code — project level
cp -r repo-polish .claude/skills/repo-polish

# OpenCode — project level
cp -r repo-polish .opencode/skill/repo-polish
```

`SKILL.md` carries only `name` and `description` in its frontmatter, so the same
file loads in both harnesses without edits.

### 2. Standalone script

Download [repo-polish.zip](https://github.com/cangurel81/repo-polish/releases/latest/download/repo-polish.zip),
unpack, and run `python scripts/repo_polish.py`.

### 3. Single file

`scripts/repo_polish.py` is self-contained. Copy that one file anywhere and run
it; nothing else is imported from the project.

## Usage

```bash
# audit the repo in the current folder
python scripts/repo_polish.py audit .

# audit a folder that is not the cwd
python scripts/repo_polish.py audit /path/to/repo --verbose

# no network at all: README, encoding, tree, licence, CI
python scripts/repo_polish.py audit . --offline

# every non-fork repo of the logged-in gh user
python scripts/repo_polish.py audit --all --verbose

# machine-readable
python scripts/repo_polish.py audit . --json

# HTTP-check every external link in the README
python scripts/repo_polish.py audit . --check-links
```

Fixing:

```bash
python scripts/repo_polish.py bom . --fix                 # strip UTF-8 BOM in place
python scripts/repo_polish.py meta . --show               # print live description/homepage/topics
python scripts/repo_polish.py meta . --apply spec.json    # write them
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

## Exit codes

| Code | Meaning |
|---|---|
| 0 | clean at the chosen threshold |
| 1 | findings at or above `--fail-on` (default `error`) |
| 2 | tool error: bad path, no `gh`, unreadable spec |

`--fail-on warn` turns the exit code into a useful CI gate. `--fail-on info` is
only useful for producing a full report.

## What it checks

| Check | Severity | What it means |
|---|---|---|
| `readme.present` | error | No README at the root |
| `readme.thin` | warn | README under 200 characters |
| `readme.bom` | error | README starts with a UTF-8 BOM |
| `readme.relative-link` | error | A relative link or image points at a missing file |
| `readme.placeholder` | warn | Leftover scaffolding: unfinished markers, template names, placeholder links |
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
| `links.bot-blocked` | note | 403/429 from a host known to block bots |

`references/checks.md` explains each one: what it catches, why it matters, and
how to fix it.

## Fix order

Cheapest and highest impact first. Each step is verifiable.

1. **Download link.** If the README says "Download" and the link 404s, nothing
   else matters. Upload the asset without a version in the file name, then link
   `releases/latest/download/<asset>`.
2. **Description and topics.** One line: what it does, on which platform. No
   "powerful", "seamless", "next-generation". Topics are the search surface.
3. **Licence.** Pick one, put the exact SPDX text in `LICENSE`, and make the
   README say the same thing.
4. **README skeleton.** What it is, screenshot or demo, install, usage, exit
   codes, licence.
5. **Encoding.** `bom . --fix`, then retype mojibake by hand — the bytes are
   already lost, transcoding them back does not work.
6. **Tree.** `git rm -r --cached dist/ build/ __pycache__/`, add them to
   `.gitignore`, ship binaries as release assets.
7. **CI.** Keep supported Node majors only, pin actions to a released tag.

## Development

```bash
python -m unittest discover -s test -v
```

50 tests, no network, no `gh`, no `git` required. Every test builds a throwaway
repository tree in a temp folder.

## Limits

- README parsing is regex-based. Unusual markup (MDX, custom components) may
  produce false positives; the finding names the exact link so it is easy to
  dismiss.
- `--check-links` uses 8 threads and caps at `--max-links` (default 200). A large
  README is truncated with a note.
- Node end-of-life dates live in a table in the script. The table is compared
  against today's date, so stale entries produce false warnings rather than
  silence.
- `--all` audits the current working directory against each remote repo's
  metadata. It is a metadata sweep, not a clone.
- The encoding checks skip any path listed in `.repo-polish-ignore` (one glob
  per line, `#` for comments). Use it for documentation that quotes damaged
  characters on purpose.
- The tool does not judge writing quality or whether the project is useful. It
  removes the reasons a visitor leaves early.

## Licence

MIT — see [LICENSE](LICENSE).
