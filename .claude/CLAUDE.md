# repo-browser — Project Rules

## Git workflow
- **Always work on the `development` branch.** Never commit directly to `main`. Ship to `main` via a GitHub PR (`gh pr create --base main --head development` → `gh pr merge <n> --merge`), NOT `git merge --ff-only` — `main` carries PR merge commits, so ff-only fails.
- **Always `git pull` before editing files or merging branches.** Remote changes may have been pushed from another session or machine.
- All git commits must be authored as James's user — do not configure or override `user.name` / `user.email`. The system gitconfig is already correct.
- Never commit release tarballs or built packages to git. They go on the GitHub Releases page only.
- The local `rb.config` is gitignored — never commit it.

## Development process
- Unless explicitly told to take an action, ask first. Do not anticipate or act on assumptions.
- After changing code, verify before saying it's ready: `python3 -c "import ast; ast.parse(open('FILE.py').read())"` for Python, and `node --check` (or a `vm.compileFunction`) on the inline `<script>` in `index.html`. Smoke-test the server end-to-end when touching request handling.

## Project structure
- `status_docs/` holds the working notes: `Engineering_plan.md`, `Project_state.md`, and `PORT_FROM_DOCUBROWSER.md` (the DocuBrowser → repo-browser feature-port ledger — update its status column as items land).
