/**
 * Math handling (milestone M2).
 *
 * Obsidian's MarkdownRenderer renders `$...$` / `$$...$$` into MathJax glyphs, which
 * drops the original LaTeX — so Anki ends up with empty <anki-mathjax> elements. To keep
 * the LaTeX, we pull math out of the raw Markdown *before* rendering, leave a placeholder
 * element for Obsidian to render around, and later swap each placeholder for the delimiter
 * form Anki preserves:
 *
 *   inline  $x$                                  -> \(x\)
 *   display $$x$$                                 -> \[x\]
 *   display with \begin{tikzcd} (or tikzpicture)  -> [$$]...[/$$]  (legacy delimiters,
 *      per the user's existing Anki MathJax/tikz setup; we do not render tikz ourselves)
 *
 * Code spans / fences are protected first so a `$` inside them is never treated as math.
 */

export type MathKind = "inline" | "display" | "tikz";

export interface MathToken {
  kind: MathKind;
  latex: string;
}

export const MATH_PLACEHOLDER_ATTR = "data-ota-math";

// A NUL byte never appears in Markdown, so it is a safe delimiter for the transient code
// placeholders (they exist only between extraction steps and are restored before rendering).
// Built via char code so no literal control character ever sits in this source file.
const NUL = String.fromCharCode(0);
const CODE_PLACEHOLDER = new RegExp(`${NUL}(\\d+)${NUL}`, "g");
const TIKZ_RE = /\\begin\{tikzcd\}|\\begin\{tikzpicture\}/;

function classifyDisplay(latex: string): MathKind {
  return TIKZ_RE.test(latex) ? "tikz" : "display";
}

/**
 * Replace math regions in `markdown` with placeholder <span> elements and return the
 * processed Markdown alongside the extracted tokens (indexed by placeholder).
 */
export function extractMath(markdown: string): { processed: string; math: MathToken[] } {
  const math: MathToken[] = [];
  const codeStore: string[] = [];

  // 1) Protect fenced (``` / ~~~) and inline (`...`) code so their `$` is ignored.
  let s = markdown.replace(/```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`/g, (match) => {
    const i = codeStore.length;
    codeStore.push(match);
    return `${NUL}${i}${NUL}`;
  });

  const placeholder = (i: number): string => `<span ${MATH_PLACEHOLDER_ATTR}="${i}"></span>`;

  // 2) Display math $$...$$ (matched before inline so $$ isn't seen as empty inline).
  s = s.replace(/(?<!\\)\$\$([\s\S]+?)(?<!\\)\$\$/g, (_m, inner: string) => {
    const latex = inner.trim();
    const i = math.length;
    math.push({ kind: classifyDisplay(latex), latex });
    return placeholder(i);
  });

  // 3) Inline math $...$ — no space just inside the delimiters, closing $ not before a
  //    digit (avoids "$5 ... $10" currency), no newline, no nested $.
  s = s.replace(/(?<!\\)\$(?!\s)([^$\n]+?)(?<![\s\\])\$(?!\d)/g, (_m, inner: string) => {
    const i = math.length;
    math.push({ kind: "inline", latex: inner.trim() });
    return placeholder(i);
  });

  // 4) Restore protected code before the caller renders anything.
  s = s.replace(CODE_PLACEHOLDER, (_m, n: string) => codeStore[Number(n)]);

  return { processed: s, math };
}

/** The Anki-facing delimiter form for a math token. */
export function delimit(token: MathToken): string {
  switch (token.kind) {
    case "inline":
      return `\\(${token.latex}\\)`;
    case "display":
      return `\\[${token.latex}\\]`;
    case "tikz":
      return `[$$]${token.latex}[/$$]`;
  }
}
