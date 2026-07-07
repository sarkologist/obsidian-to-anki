// Deploy the built plugin into an Obsidian vault's plugin folder.
//
//   npm run install-plugin                 # -> default vault (dissertation)
//   OBSIDIAN_VAULT=/path/to/vault npm run install-plugin
//
// Copies main.js + manifest.json (+ styles.css if present) into
// <vault>/.obsidian/plugins/<plugin-id>/. Obsidian must be told to enable the
// plugin once (Settings -> Community plugins), and reloaded to pick up changes.

import { readFileSync, existsSync, mkdirSync, copyFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const pluginRoot = resolve(scriptDir, "..");

const DEFAULT_VAULT = "/Users/sark/Dropbox/projects/dissertation";
const vault = resolve(process.env.OBSIDIAN_VAULT || DEFAULT_VAULT);

function fail(message) {
  console.error(`install: ${message}`);
  process.exit(1);
}

if (!existsSync(vault) || !statSync(vault).isDirectory()) {
  fail(`vault not found: ${vault}`);
}
const obsidianDir = join(vault, ".obsidian");
if (!existsSync(obsidianDir)) {
  fail(`${vault} is not an Obsidian vault (no .obsidian folder).`);
}

const manifest = JSON.parse(readFileSync(join(pluginRoot, "manifest.json"), "utf8"));
const pluginId = manifest.id;
if (!pluginId) fail("manifest.json is missing an \"id\".");

const targetDir = join(obsidianDir, "plugins", pluginId);
mkdirSync(targetDir, { recursive: true });

// main.js is required; styles.css is optional.
const required = ["main.js", "manifest.json"];
const optional = ["styles.css"];

for (const name of required) {
  const src = join(pluginRoot, name);
  if (!existsSync(src)) fail(`missing build output: ${name} (run "npm run build" first)`);
  copyFileSync(src, join(targetDir, name));
}
for (const name of optional) {
  const src = join(pluginRoot, name);
  if (existsSync(src)) copyFileSync(src, join(targetDir, name));
}

console.log(`install: deployed "${pluginId}" v${manifest.version} -> ${targetDir}`);
console.log("install: enable it in Obsidian (Settings -> Community plugins) and reload if already enabled.");
