# Obsidian → Anki: design plan

Send selected Markdown (math, tables, images) from Obsidian directly into an Anki
editor field, replacing the old Alfred → Haskell → Node relay.

## The shape of the change

Keep the part that works (the paste-bridge Anki addon) and delete the fragile relay
(Alfred → `markdown-to-html` (Haskell) → Node). The conversion moves *into* Obsidian,
where it has the vault's knowledge (image embeds, wikilinks, math setup).

```
BEFORE:  Obsidian copy → Alfred → markdown-to-html (Haskell) → node bridge → HTTP → Anki addon → doPaste
AFTER:   Obsidian plugin (select → convert → upload media → POST) ─────────── HTTP → Anki addon → doPaste
```

Two components:

1. **Obsidian plugin** (new) — capture, convert, send.
2. **Bridge addon, extended** (`anki-addon/`) — gains a `/media` endpoint and a
   "remember last field" tweak. No AnkiConnect, no second service.

Retired once proven: the Alfred workflows, the `markdown-to-html` Haskell binary, and
`insert-html-into-active-anki-editor.js`.

## Component 1 — the Obsidian plugin

**Trigger:** a command-palette command, *"Send selection to Anki"* (user binds a hotkey).
Operates on the current selection only; no selection → no-op with a toast.

**Pipeline** (order matters):

1. Grab the raw selected Markdown from the active editor.
2. **Protect math first**, before any HTML rendering. Scan for `$…$` / `$$…$$`
   (skipping code spans/fences), replace each with a placeholder, and classify:
   - inline → `\(…\)`
   - display, no tikz → `\[…\]`
   - display containing `\begin{tikzcd}` → **legacy `[$$]…[/$$]`** (passthrough to the
     user's existing Anki MathJax/tikz setup; the plugin does NOT render tikzcd)
3. **Render the rest through Obsidian's own `MarkdownRenderer.render()`** into a detached
   container — buys fidelity for tables, callouts, formatting, and `![[image]]` resolution.
4. **Post-process the HTML for Anki:**
   - Reinsert math placeholders with the delimiters chosen above.
   - Images: read attachment bytes, name by content hash (dedup), `POST` to the bridge's
     `/media` endpoint, rewrite `<img src>` to the returned bare filename.
   - Strip Obsidian-specific wrapper divs / classes / inline styles; unwrap the container.
   - Wikilinks → plain text (drop dead `obsidian://` hrefs); keep real external links.
     *(Toggle in settings.)*
5. `POST` the final HTML to the bridge's existing `/insert` endpoint. Toast on result.

**Zero-config connection:** read the discovery file the Node script used —
`~/Library/Application Support/Anki2/alfred-anki-bridge.json` — for URL + token. Obsidian
desktop runs in Electron with Node access, so this just works.

**Settings:** wikilink handling, strip leading note title, image max-width, bridge-file
path override, append-source-link toggle.

**Source backlink:** if the target note has a `source` field, the plugin also hands the
bridge the current note's `obsidian://open?vault=…&file=…` URL, which the addon appends to
that field on a new line (as a clickable link, deduped so re-sends don't stack). Notes
without a `source` field are unaffected. Toggle in settings.

## Component 2 — bridge addon changes

Two additions to `anki-addon/__init__.py`:

1. **`POST /media`** — same token auth, localhost-only. Accepts file bytes + suggested
   name, writes into `collection.media` (dedup by content hash), returns the stored
   filename.
2. **Remember the last-focused field.** Today `_active_editor()` requires a currently
   focused field, which fails the moment focus moves to Obsidian. Track the last
   editor + field index and fall back to it when nothing is focused, inserting into that
   field even while Anki is backgrounded.
3. **Append the Obsidian backlink.** `/insert` accepts an optional `source_url` query
   param; when the target note has a `source` field, append it there (deduped) via
   `loadNote` so it persists on the note's next save, then re-focus the paste target.

**⚠️ Main technical risk (de-risked in M0):** `doPaste` expects an active field/caret.
Backgrounded + blurred, we must re-assert `currentField` and focus the field
programmatically before pasting — ideally *without* raising Anki's window (to keep the
user in Obsidian). If that proves flaky, fallback is a two-step (plugin stages HTML, user
tabs to Anki + one hotkey pastes).

## Deferred / revisit

- **tikzcd long-term:** parked — MathJax/TikZJax support too limited. Legacy `[$$]`
  passthrough for now.
- **Anki CSS vs Obsidian HTML** — cleanup pass may need tuning against real note types.
- **Embeds/transclusions of other notes** inside a selection — inline or drop, TBD.

## Milestones

- **M0 — de-risk:** background-field insertion in the addon. Nothing else until solid.
- **M1:** plugin scaffold; plain-text selection → HTML → `/insert` (no math, no images).
- **M2:** math classification + delimiter emission (`\(…\)`, `\[…\]`, `[$$]` for tikzcd).
- **M3:** `/media` endpoint + image upload/rewrite.
- **M4:** cleanup pass, wikilink handling, settings.
