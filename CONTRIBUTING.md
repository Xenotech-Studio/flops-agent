# Contributing to flops_agent

Thanks for contributing. This repository is a Python 3.10+ project; please keep
the framework independent of any particular application, deployment, or
configuration source.

## Development setup

From a fresh checkout, create and activate a virtual environment, then run:

```bash
python -m pip install -e .[dev]
npm ci
```

Run the required checks before opening a pull request:

```bash
bash scripts/run-tests.sh
bash scripts/run-pyright.sh
```

`run-pyright.sh` uses the repository's pinned Node tooling, so `npm ci` is
required in addition to the Python installation.

## Git hooks

Forks can opt into the repository hooks with this command:

```bash
git config core.hooksPath .githooks
```

Maintainers: do not run that command in an environment that already uses a
personal hook-distribution layer. It replaces that environment's `core.hooksPath`
setting and can bypass its chain. Keep the existing distribution layer in place
and ensure it delegates to this repository's hooks.

The hooks validate commit messages and run the test and Pyright checks. Do not
use `--no-verify` to bypass them.

## Commits

Use Conventional Commit-style subjects:

```text
type(scope): concise imperative description
```

Allowed types are `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`,
`build`, `ci`, `chore`, and `revert`. Use a `!` only for a breaking change and
include a matching `BREAKING CHANGE:` footer.

## Pull requests

The repository is currently private, but contributions should follow the normal
pull-request workflow:

1. Create a focused branch from the current default branch.
2. Add or update tests and documentation with the change.
3. Run both required checks locally without bypassing hooks.
4. Describe the user-visible behavior, compatibility impact, and verification in
   the pull request.
5. Keep unrelated formatting and generated files out of the pull request.

For changes to public names, seams, wire behavior, persistence, or crypto,
explain the compatibility rationale explicitly.
