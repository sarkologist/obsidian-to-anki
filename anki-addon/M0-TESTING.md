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
  `focusField(idx)` moves the caret to the end, so the addon freezes the real caret first
  (via Anki's `require("anki/location").saveSelection`) and restores it just before pasting.
  Best-effort: if the location package is unavailable or the coordinates no longer resolve,
  it falls back to Anki's end-of-field behaviour.
- The `/insert` response now includes diagnostics: `from_memory`, `was_focused`,
  `target_field_index`, `raised_window`, `restored_caret` (whether the caret
  freeze/restore was attempted — the background path).

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
4. **Pass:** the field reads `fooXYZbar` — inserted at the caret. The JSON response shows
   `"restored_caret": true`. **Fail:** it reads `foobarXYZ` (appended at the end), which
   means the freeze/restore didn't take (older Anki without `anki/location`, or a resolve
   failure) and it fell back to end-of-field.

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
