# Agent working policy

## Pull requests for every change

- **Never commit directly to `main`.** All changes land via a pull request.
- For each unit of work: branch off `main` (`git switch -c <short-kebab-name>`), commit,
  push with `-u`, and open a PR with `gh pr create`.
- Keep PRs scoped to one milestone / concern (see `PLAN.md` milestones).
- Commit messages end with the trailer:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- PR bodies end with:
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`
- Do not merge PRs automatically. Leave merging to the user unless explicitly asked.

## Codex reviews every PR

After opening a PR, run a Codex review locally and post it as a PR comment:

```bash
codex review --base main > /tmp/codex-review.md
gh pr comment <PR#> --body-file /tmp/codex-review.md
```

- Run from the PR branch checked out locally (Codex is authenticated via ChatGPT login).
- Prefix the posted comment so its origin is clear, e.g. a `## 🤖 Codex review` heading.
- If Codex raises issues, summarize them for the user and offer to address them in the
  same PR before merge — do not silently ignore review findings.

## Project layout

- `anki-addon/` — the companion Anki addon (HTTP bridge; extended per `PLAN.md`).
- `plugin/` — the Obsidian plugin (added from M1 onward).
- `PLAN.md` — the living design + milestone plan. Update it when decisions change.
