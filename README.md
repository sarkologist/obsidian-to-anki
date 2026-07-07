# obsidian-to-anki

Send selected Markdown — including math, tables, and images — from Obsidian straight into
the focused Anki editor field.

This replaces an older Alfred → Haskell → Node relay with:

- an **Obsidian plugin** that converts the selection and posts it to Anki, and
- a small **Anki addon** (`anki-addon/`) that receives HTML and pastes it into the active
  editor field via `editor.doPaste`, sidestepping clipboard flakiness.

See [`PLAN.md`](PLAN.md) for the full design and milestones, and [`CLAUDE.md`](CLAUDE.md)
for the contribution/review workflow.

## Status

Early. `anki-addon/` is the existing bridge addon; the Obsidian plugin and the addon
extensions (media endpoint, background-field insert) are in progress — see the milestones
in `PLAN.md`.

## Components

| Path          | What it is                                                        |
| ------------- | ----------------------------------------------------------------- |
| `anki-addon/` | Anki companion addon: localhost HTTP bridge, token-authenticated. |
| `plugin/`     | Obsidian plugin (from M1).                                        |
