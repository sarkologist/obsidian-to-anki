# M0 — background-field insert: how to test

The whole "trigger from Obsidian" design rests on one assumption: **the addon can paste
into the field you last used in Anki even while Anki is in the background.** This change
implements that; these steps confirm whether it actually works on your machine.

## Install the addon

Deploy this folder into Anki's addon directory as static files:

```bash
node anki-addon/scripts/install.mjs
# or, for a non-default Anki location:
ANKI_ADDONS_DIR=/path/to/addons21 node anki-addon/scripts/install.mjs
```

Restart Anki to load the new code. Re-run the script (and restart) after each change.

> Don't symlink `addons21/<package>/__init__.py` at the repo file. It's tempting, but with
> git worktrees the symlink pins Anki to one branch's checkout and dangles once that
> worktree is removed. The script copies static files so the running addon is decoupled from
> the working tree (it also replaces any leftover symlink from the old approach).

## What changed

- The bridge now remembers the **last field you were in** — captured primarily on field
  unfocus (`editor_did_unfocus_field`, which fires the moment focus leaves the field and
  carries the field index), with the typing timer as a backup — and, on `/insert`, falls
  back to that field when nothing is live-focused. The memory is tagged with the note id
  and discarded if the editor later loads a different note, so a stale index can't paste
  into the wrong note.
- When the target field is blurred (Anki backgrounded), it re-focuses that field
  (`web.setFocus()` + `focusField(idx)`) before `doPaste`, **without raising the Anki
  window** by default.
- The insert lands **at the caret position within that field**, not at the field's end.
  Anything that focuses a field ends in Anki's `moveCaretToEnd()`, so the addon snapshots the
  real caret (via `require("anki/location").saveSelection`) **before it touches the editor at
  all**, and restores it just before pasting. Best-effort: if the location package is
  unavailable or the coordinates no longer resolve, it falls back to end-of-field.
- After the insert the **caret sits after what was inserted**, so you can keep typing where
  the pasted content ends. Anki's own paste doesn't guarantee that: it re-decorates MathJax
  and unwraps headings *after* `execCommand("insertHTML")`, and re-creating the last inserted
  node drops the caret back in front of the paste. The addon bookmarks the end of the paste
  target first and puts the caret on the bookmark once the paste settles.
- The `/insert` response includes diagnostics: `from_memory`, `was_focused`,
  `target_field_index`, `raised_window`, `source_updated`, and `restored_caret` — the value
  the webview actually reported (`true`/`false`), or `null` when no restore was needed
  because the field was already focused with the caret live.

## Test A — the real scenario (backgrounded insert)

1. Open Anki's Add window, click into a field, type a couple of characters (this seeds the
   remembered field), then leave the caret there.
2. Switch to another app (Obsidian, or just Terminal) so Anki is no longer frontmost.
3. From Terminal, hit the bridge directly (uses the discovery file for URL + token):

   ```bash
   node -e '
     const fs=require("fs");
     const b=JSON.parse(fs.readFileSync(process.env.HOME+"/Library/Application Support/Anki2/alfred-anki-bridge.json","utf8"));
     fetch(b.url,{method:"POST",headers:{Authorization:"Bearer "+b.token,"Content-Type":"text/html"},body:"<b>hello from background</b>"})
       .then(r=>r.text()).then(t=>console.log(t));
   '
   ```

4. **Pass:** the text appears in the field you seeded, Anki stays in the background, and
   the JSON response shows `"ok": true, "from_memory": true, "was_focused": false`.
   **Fail:** empty response error, or the text lands nowhere / in the wrong field.

## Test C — caret position within the field

Confirms the paste lands where your cursor was, not appended at the end.

1. Open the Add window, click into a field and type `foobar`.
2. Click (or arrow) to place the caret **between `foo` and `bar`**, then switch to another
   app so Anki is backgrounded.
3. Run the Test A command but with a distinctive body, e.g. `body:"XYZ"`.
4. **Pass:** the field reads `fooXYZbar` — inserted at the caret, and the response shows
   `"restored_caret": true`. **Fail:** it reads `foobarXYZ` (appended at the end).

The field content is the real signal, not `restored_caret` — a restore can report `true` and
still land at the end if the caret was snapshotted *after* something moved it (that was the
Test D bug). Trust the text; use the flag to narrow down why.

## Test D — caret + source link, in the Browse window

The regression that Test C missed. The source-link feature calls `editor.loadNote()`, which
re-renders the fields and parks the caret at the end — so the caret has to be snapshotted
before that runs, not after.

1. Enable **Append source link** in the plugin, and pick a note whose notetype **has a
   `Source` field** — the reload only happens when the link is actually appended.
2. Open the **Browse** window, click that note, put the caret **between `foo` and `bar`** in
   a field that already has text.
3. Switch to Obsidian and send a selection whose Obsidian URL is *not already* in the note's
   `Source` field (a fresh source triggers the append + reload).
4. **Pass:** `fooXYZbar`, and the response shows `"source_updated": true` with
   `"restored_caret": true`. **Fail:** `foobarXYZ`.

This hides in the Add window: there the first paste — the one that appends the link and
triggers the reload — usually goes into an *empty* field, where "end of field" and "at the
caret" are the same place.

## Test E — the caret ends up after the insert

Confirms you can carry on typing from the end of what was just inserted.

1. Open the Add window, click into a field and type `foobar`, caret between `foo` and `bar`.
2. Switch to another app.
3. Send a selection containing **math**, e.g. `$x^2$` in Obsidian (plain text won't catch the
   regression — only content Anki re-decorates after the paste does).
4. Switch back to Anki and type `Z` without clicking anywhere.
5. **Pass:** `Z` sits between the rendered math and `bar`. **Fail:** the field reads
   `fooZ<math>bar` — the caret was left in front of the insert.

## Test B — fallback with window raise

If Test A pastes nowhere, the webview likely won't accept focus while hidden. Retry with
the window-raise fallback to confirm that path works:

```bash
BRIDGE_RAISE_ON_INSERT=1 /path/to/anki   # or set it in the env Anki launches from
```

Re-run Test A. If it now works with `"raised_window": true`, we know the gentle path is
insufficient on your setup and the plugin flow should either raise the window or use a
two-step paste. Record which path worked in the PR.

## Notes / known unknowns

- `focusField(idx)` is Anki's editor JS; if a future Anki renames it this breaks — the
  diagnostics will show the paste not landing.
- Capture now happens on unfocus, so simply clicking a field and then leaving (even
  without typing) should seed the target. If a given Anki version doesn't fire
  `editor_did_unfocus_field`, the typing-timer backup still covers the type-then-switch
  case.
