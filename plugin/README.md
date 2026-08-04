# Obsidian to Anki — plugin

Obsidian plugin that sends the current Markdown selection into the focused Anki editor
field, via the local bridge add-on in [`../anki-addon`](../anki-addon).

Status: **M1** — plain selection → HTML → `/insert`. Math delimiters (M2) and image
media upload (M3) are not implemented yet. See [`../PLAN.md`](../PLAN.md).

## Build & install

```bash
cd plugin
npm install            # once
npm run build          # type-check + bundle to main.js
npm run install-plugin # build, then copy into the target vault's plugin folder
```

`install-plugin` deploys `main.js`, `manifest.json`, and `styles.css` into
`<vault>/.obsidian/plugins/obsidian-to-anki/`. The vault defaults to
`/Users/sark/Dropbox/projects/dissertation`; override it with:

```bash
OBSIDIAN_VAULT=/path/to/another/vault npm run install-plugin
```

After installing, enable **Obsidian to Anki** in Obsidian's *Settings → Community
plugins*, and reload the plugin (or the app) when you redeploy.

For live iteration, `npm run dev` starts esbuild in watch mode; re-run `install-plugin`
(or symlink the plugin folder) to pick up changes.

## Usage

1. Ensure Anki is running with the bridge add-on enabled (it writes the discovery file
   the plugin reads).
2. In Anki, click the editor field you want to target.
3. In Obsidian, select some Markdown and run the command **"Send selection to Anki"**
   (bind a hotkey in *Settings → Hotkeys*).

Selecting part of a table works: pick a few rows and the plugin puts the table's header and
delimiter rows back before sending, so Anki gets a table rather than a paragraph of `|`.
Rows the selection only half covers are widened to the whole row.

The bridge discovery file path can be overridden in the plugin's settings tab.
