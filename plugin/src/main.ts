import {
  App,
  Component,
  Editor,
  MarkdownRenderer,
  MarkdownView,
  Notice,
  Plugin,
  PluginSettingTab,
  Setting,
  requestUrl,
} from "obsidian";
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { basename, join } from "node:path";
import { MATH_PLACEHOLDER_ATTR, delimit, extractMath } from "./math";

/**
 * Milestone M1: send the current Markdown selection into the focused Anki editor
 * field. Renders via Obsidian's own MarkdownRenderer for fidelity and posts the HTML
 * to the local bridge add-on. Math delimiter handling (M2) and image media upload
 * (M3) are intentionally not done here.
 */

interface OtaSettings {
  /** Override path to the bridge discovery file; empty = platform default. */
  bridgeFilePath: string;
}

const DEFAULT_SETTINGS: OtaSettings = {
  bridgeFilePath: "",
};

function defaultBridgeFile(): string {
  // Matches where the Anki bridge add-on writes its discovery file.
  return join(
    homedir(),
    "Library",
    "Application Support",
    "Anki2",
    "alfred-anki-bridge.json",
  );
}

interface BridgeInfo {
  url: string;
  token: string;
}

export default class ObsidianToAnkiPlugin extends Plugin {
  settings: OtaSettings = DEFAULT_SETTINGS;

  async onload(): Promise<void> {
    await this.loadSettings();

    this.addCommand({
      id: "send-selection-to-anki",
      name: "Send selection to Anki",
      editorCheckCallback: (checking, editor, view) => {
        const hasSelection = editor.getSelection().trim().length > 0;
        if (checking) return hasSelection;
        void this.sendSelection(editor, view as MarkdownView);
        return true;
      },
    });

    this.addSettingTab(new OtaSettingTab(this.app, this));
  }

  private async sendSelection(editor: Editor, view: MarkdownView): Promise<void> {
    const markdown = editor.getSelection();
    if (!markdown.trim()) {
      new Notice("Obsidian → Anki: nothing selected.");
      return;
    }

    let html: string;
    try {
      html = await this.renderSelection(markdown, view);
    } catch (err) {
      new Notice(`Obsidian → Anki: render failed — ${errorMessage(err)}`);
      return;
    }

    try {
      await this.postToBridge(html);
      new Notice("Obsidian → Anki: sent.");
    } catch (err) {
      new Notice(`Obsidian → Anki: send failed — ${errorMessage(err)}`);
    }
  }

  private async renderSelection(markdown: string, view: MarkdownView): Promise<string> {
    // Pull math out before rendering so the LaTeX survives (Obsidian would otherwise render
    // it to MathJax glyphs and Anki would receive empty <anki-mathjax> elements).
    const { processed, math } = extractMath(markdown);

    const container = document.createElement("div");
    const component = new Component();
    // sourcePath lets Obsidian resolve embeds/wikilinks relative to the current note.
    const sourcePath = view?.file?.path ?? "";
    try {
      await MarkdownRenderer.render(this.app, processed, container, sourcePath, component);
      this.restoreMath(container, math);
      await this.processImages(container, sourcePath);
      return container.innerHTML;
    } finally {
      component.unload();
    }
  }

  /**
   * Upload local images into Anki's media collection and rewrite each to the stored
   * filename. Internal embeds (![[img]]) are resolved through the vault API — robust to the
   * inner <img> not having loaded in a detached container — while plain <img> tags with a
   * local resource src are read from disk. Remote (http/https) and inline (data:) are left
   * as-is.
   */
  private async processImages(container: HTMLElement, sourcePath: string): Promise<void> {
    const embeds = Array.from(container.querySelectorAll<HTMLElement>(".image-embed[src]"));
    const plainImgs = Array.from(container.querySelectorAll("img")).filter(
      (img) => localPathFromSrc(img.getAttribute("src")) !== null,
    );
    if (embeds.length === 0 && plainImgs.length === 0) return;

    const bridge = this.readBridgeInfo();
    let failures = 0;

    // 1) Internal embeds: resolve the linkpath via the vault, not the async-loaded <img>.
    for (const embed of embeds) {
      const linkpath = embed.getAttribute("src") ?? "";
      const file = this.app.metadataCache.getFirstLinkpathDest(linkpath, sourcePath);
      if (!file) {
        failures += 1;
        continue;
      }
      try {
        const bytes = await this.app.vault.readBinary(file);
        const filename = await this.uploadMedia(bridge, bytes, file.name);
        const img = document.createElement("img");
        img.setAttribute("src", filename);
        embed.replaceWith(img);
      } catch {
        failures += 1;
      }
    }

    // 2) Plain <img> tags pointing at a local resource (e.g. ![](local.png)).
    for (const img of plainImgs) {
      const path = localPathFromSrc(img.getAttribute("src"));
      if (!path) continue;
      try {
        const data = readFileSync(path);
        const bytes = data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) as ArrayBuffer;
        const filename = await this.uploadMedia(bridge, bytes, basename(path));
        img.setAttribute("src", filename);
        for (const attr of ["alt", "referrerpolicy", "loading", "draggable"]) {
          img.removeAttribute(attr);
        }
      } catch {
        failures += 1;
      }
    }

    if (failures > 0) {
      new Notice(`Obsidian → Anki: ${failures} image(s) could not be uploaded.`);
    }
  }

  private async uploadMedia(bridge: BridgeInfo, bytes: ArrayBuffer, name: string): Promise<string> {
    const url = new URL(bridge.url);
    url.pathname = "/media";
    url.searchParams.set("name", name);
    const response = await requestUrl({
      url: url.toString(),
      method: "POST",
      headers: {
        Authorization: `Bearer ${bridge.token}`,
        "Content-Type": "application/octet-stream",
      },
      body: bytes,
      throw: false,
    });
    if (response.status < 200 || response.status >= 300) {
      throw new Error(`media upload ${response.status}: ${response.text}`);
    }
    const filename = (response.json as { filename?: string } | undefined)?.filename;
    if (!filename) throw new Error("media response missing filename");
    return filename;
  }

  /** Swap each math placeholder for the Anki delimiter form of its LaTeX. */
  private restoreMath(container: HTMLElement, math: ReturnType<typeof extractMath>["math"]): void {
    const placeholders = container.querySelectorAll(`[${MATH_PLACEHOLDER_ATTR}]`);
    let missing = 0;
    placeholders.forEach((el) => {
      const idx = Number(el.getAttribute(MATH_PLACEHOLDER_ATTR));
      const token = math[idx];
      if (!token) {
        missing += 1;
        return;
      }
      el.replaceWith(document.createTextNode(delimit(token)));
    });
    if (placeholders.length < math.length) {
      // Some placeholders were dropped by the renderer — warn rather than silently lose math.
      new Notice(
        `Obsidian → Anki: ${math.length - placeholders.length + missing} math block(s) may have been lost in rendering.`,
      );
    }
  }

  private readBridgeInfo(): BridgeInfo {
    const path = this.settings.bridgeFilePath.trim() || defaultBridgeFile();
    let raw: string;
    try {
      raw = readFileSync(path, "utf8");
    } catch {
      throw new Error(
        `could not read bridge file at ${path}. Is Anki running with the bridge add-on enabled?`,
      );
    }
    let parsed: Partial<BridgeInfo>;
    try {
      parsed = JSON.parse(raw) as Partial<BridgeInfo>;
    } catch {
      throw new Error(`bridge file at ${path} is not valid JSON.`);
    }
    if (!parsed.url || !parsed.token) {
      throw new Error(`bridge file at ${path} is missing url/token.`);
    }
    return { url: parsed.url, token: parsed.token };
  }

  private async postToBridge(html: string): Promise<void> {
    const bridge = this.readBridgeInfo();
    // requestUrl runs outside the renderer's fetch, so it isn't subject to CORS.
    const response = await requestUrl({
      url: bridge.url,
      method: "POST",
      headers: {
        Authorization: `Bearer ${bridge.token}`,
        "Content-Type": "text/html; charset=utf-8",
      },
      body: html,
      throw: false,
    });
    if (response.status < 200 || response.status >= 300) {
      throw new Error(`bridge returned ${response.status}: ${response.text}`);
    }
  }

  async loadSettings(): Promise<void> {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings(): Promise<void> {
    await this.saveData(this.settings);
  }
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/**
 * Map a rendered <img src> to a local filesystem path, or null if it isn't a local vault
 * resource. Obsidian renders vault images to app:// (and occasionally file://) URLs whose
 * pathname is the absolute file path; remote (http/https) and inline (data:) are skipped.
 */
function localPathFromSrc(src: string | null): string | null {
  if (!src || /^(https?|data):/i.test(src)) return null;
  if (!src.startsWith("app://") && !src.startsWith("file://")) return null;
  try {
    return decodeURIComponent(new URL(src).pathname);
  } catch {
    return null;
  }
}

class OtaSettingTab extends PluginSettingTab {
  constructor(app: App, private readonly plugin: ObsidianToAnkiPlugin) {
    super(app, plugin);
  }

  display(): void {
    const { containerEl } = this;
    containerEl.empty();

    new Setting(containerEl)
      .setName("Bridge discovery file")
      .setDesc(
        "Path to the Anki bridge add-on's discovery file. Leave empty to use the default " +
          `(${defaultBridgeFile()}).`,
      )
      .addText((text) =>
        text
          .setPlaceholder(defaultBridgeFile())
          .setValue(this.plugin.settings.bridgeFilePath)
          .onChange(async (value) => {
            this.plugin.settings.bridgeFilePath = value;
            await this.plugin.saveSettings();
          }),
      );
  }
}
