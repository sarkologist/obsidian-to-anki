// Deploy this Anki add-on into Anki's add-on folder as static files.
//
//   node anki-addon/scripts/install.mjs                 # -> default Anki2/addons21
//   ANKI_ADDONS_DIR=/path/to/addons21 node anki-addon/scripts/install.mjs
//
// Copies __init__.py + manifest.json into <addons21>/<package>/ (the package name comes
// from manifest.json). Anki must be restarted to pick up the new code.
//
// Why copy instead of symlink: an earlier dev setup symlinked <addons21>/<package>/__init__.py
// straight at the repo file. With git worktrees that back-fires — the symlink pins Anki to
// one branch's checkout, and a removed worktree leaves it dangling. Static files decouple the
// running add-on from the working tree, exactly like the Obsidian plugin's install script.

import { readFileSync, existsSync, mkdirSync, copyFileSync, lstatSync, rmSync, unlinkSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const addonRoot = resolve(scriptDir, "..");

function fail(message) {
  console.error(`install: ${message}`);
  process.exit(1);
}

function defaultAddonsDir() {
  // macOS location; override with ANKI_ADDONS_DIR elsewhere.
  return join(homedir(), "Library", "Application Support", "Anki2", "addons21");
}

const manifest = JSON.parse(readFileSync(join(addonRoot, "manifest.json"), "utf8"));
const pkg = manifest.package;
if (!pkg) fail('manifest.json is missing a "package" name.');

const addonsDir = resolve(process.env.ANKI_ADDONS_DIR || defaultAddonsDir());
if (!existsSync(addonsDir)) {
  fail(`Anki add-ons dir not found: ${addonsDir} (is Anki installed? set ANKI_ADDONS_DIR).`);
}

const targetDir = join(addonsDir, pkg);
mkdirSync(targetDir, { recursive: true });

// Files the add-on needs at runtime. M0-TESTING.md and scripts/ stay in the repo only.
const files = ["__init__.py", "manifest.json"];

for (const name of files) {
  const src = join(addonRoot, name);
  if (!existsSync(src)) fail(`missing source file: ${name}`);
  const dest = join(targetDir, name);
  // A prior dev install may have symlinked this at the repo; copying onto a symlink would
  // write *through* it and clobber the repo file, so replace the link with a real file.
  if (existsSync(dest) && lstatSync(dest).isSymbolicLink()) unlinkSync(dest);
  copyFileSync(src, dest);
}

// Drop stale bytecode from any previous (possibly symlinked) install so Anki recompiles.
rmSync(join(targetDir, "__pycache__"), { recursive: true, force: true });

console.log(`install: deployed "${pkg}" -> ${targetDir}`);
console.log("install: restart Anki to load the new add-on code.");
