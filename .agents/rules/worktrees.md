# Git Worktree Testing & Commits

- **Symlink `.venv` for Pre-Commit Hooks**: The pre-commit `pytest` hook invokes `.venv/bin/pytest` relative to the worktree root. When working in a git worktree without its own virtual environment, symlink the main checkout's `.venv` (`ln -s <main-repo>/.venv .venv`).
- **Set `PYTHONPATH="$PWD/src"`**: A symlinked `.venv` resolves its editable install `.pth` back to `<main-repo>/src`. Prefix `.venv/bin/pytest` and `git commit` in a symlinked-`.venv` worktree with `PYTHONPATH="$PWD/src"` (and `UV_FROZEN=1` on `git commit` so `uv run` hooks do not mutate `uv.lock`) so tests execute against the worktree's own `src/`.
