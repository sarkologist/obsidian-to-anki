# M0 — background-field insert: how to test

The whole "trigger from Obsidian" design rests on one assumption: **the addon can paste
into the field you last used in Anki even while Anki is in the background.** This change
implements that; these steps confirm whether it actually works on your machine.

## Install the dev addon

Symlink (or copy) this folder into Anki's addon directory so edits are picked up:

```bash
ln -s "$(pwd)/anki-addon" \
  "$HOME/Library/Application Support/Anki2/addons21/alfred_anki_html_bridge_dev"
```

Restart Anki. (Disable the old packaged copy of the addon so they don't both bind.)

## What changed

- The bridge now remembers the **last field you were typing in** (via the editor typing
  timer) and, on `/insert`, falls back to that field when nothing is live-focused.
- When the target field is blurred (Anki backgrounded), it re-focuses that field
  (`web.setFocus()` + `focusField(idx)`) before `doPaste`, **without raising the Anki
  window** by default.
- The `/insert` response now includes diagnostics: `from_memory`, `was_focused`,
  `target_field_index`, `raised_window`.

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
- If you never typed in the field (only clicked), the typing-timer may not have fired;
  clicking + one keystroke guarantees the field is remembered. A later iteration can also
  capture pure focus (no keystroke) if needed.
