# How PudiSync is built

One rule: every change is a small unit that can be named, tested, traced to a
build, and undone.

## Branches

| Branch | For | Ends |
|---|---|---|
| `main` | Releases only. Every commit on it is a tagged release. | never deleted |
| `feature/color-engine` | Integration: where finished features meet and are tried together. | merged into `main` for a release |
| `feat/<name>` | One feature. Cut from the integration branch, in its own worktree. | merged, then deleted |
| `fix/<name>` | One bug, with the test that failed first. | merged, then deleted |
| `exp/<name>` | An experiment (see below). Never merged as is. | deleted; the note stays |

`prototype-v1` is a tag: the first prototype, frozen. `git checkout prototype-v1` always restores it.

A feature is one branch, one worktree (`git worktree add .claude/worktrees/<name> -b feat/<name> feature/color-engine`),
one pull request, merged with a merge commit so it stays one visible unit in history.

## What a feature must carry

- A test that fails without it (a bug fix: the failing repro first).
- A line under *Unreleased* in `CHANGELOG.md`.
- The checks it ran, as command and result, in the PR description.
- A statement of what it did not verify. Software tests do not prove the wiring, the heat or how the light looks.

Run the suite on your own ports: `AMBILIGHT_TEST_PORT_BASE=<base> make test`.
It needs an awake, unlocked display and a Mac, so it runs locally rather than in CI; the PR records the result.

## Versions

`VERSION` is the single source, read by `ambilight/version.py`. It follows
[SemVer](https://semver.org): a change that breaks a saved config or an API is
major, a new feature is minor, a fix is patch.

- During development it reads `X.Y.Z-dev`. Tagged milestones on the integration
  branch are `vX.Y.Z-alpha.N`, then `-beta.N`, then `-rc.N`.
- A release is the integration branch merged into `main` and tagged `vX.Y.Z` (annotated).
- A saved `config.json` must keep loading. Unknown keys are dropped, so renaming a
  setting needs a migration in `config.py`.

## Experiments

An experiment answers one question with numbers, for example "does DDP beat UDP
on this board?" or "which mode scores best against the wall?".

1. Branch `exp/<name>`; copy `experiments/TEMPLATE.md` to `experiments/YYYY-MM-DD-<name>.md`.
2. Write the hypothesis and the pass mark **before** running it.
3. Run it, paste the commands and numbers, and record the build id.
4. Decide: a winner is rebuilt as a clean `feat/<name>`; a loser is deleted. Either
   way the note is committed, so the result is never lost.

Looks (Vivid, Immersive) are the toggles for visual experiments.
Output and capture choices are the plugin registries in `ambilight/plugins/`.
There is no feature-flag system; add one only when an experiment needs two
variants in the same build.

## What is recorded

| Record | Where | Holds |
|---|---|---|
| Build id | `/api/status` `version`, the startup log line | `2.0.0-dev+193d10f`: version and commit |
| Settings journal | `config.journal.jsonl` beside `config.json` (git-ignored, rolls at 1 MB) | time, build, and old and new value of every setting that changed; the PIN is never written |
| Run log | `~/Library/Logs/ambilight.log` | the hub's own log |
| Measurements | `experiments/*.md` | latency, match score, test counts, per build |

Put together: any number in an experiment note can be traced to a build id, and
the journal says what the settings were at that time.

## Hard rules

They live in `CLAUDE.md` (camera, live hardware, secrets, test ports, git). They
apply to every branch and every experiment.
