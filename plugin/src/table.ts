/**
 * Partial table selections.
 *
 * A selection of a few body rows is not a table to any Markdown parser — the header row
 * and the `|---|---|` delimiter row that follows it are what make it one, and they sit
 * above the selection:
 *
 *   | Fact | Form used | Spent at |     <- header      (not selected)
 *   | ---- | --------- | -------- |     <- delimiter   (not selected, invisible when rendered)
 *   | D1 … | $L(s,χ)…$ | Step 2b… |     <- selection starts here
 *
 * Sent as-is, Obsidian renders that as a paragraph full of literal `|`. So before anything
 * else in the pipeline, walk up from the selection to find the table it started in and put
 * its header and delimiter back on the front. Selections that don't start in a table are
 * returned exactly as the editor has them.
 *
 * The rows themselves are handed over verbatim, so whatever they contain — math, images,
 * links — flows through the rest of the pipeline as usual.
 */

export interface Pos {
  line: number;
  ch: number;
}

/** The slice of Obsidian's Editor this module needs (structural, so it stubs easily). */
export interface EditorLines {
  getLine(line: number): string;
  lastLine(): number;
  getRange(from: Pos, to: Pos): string;
}

/**
 * A table row inside a callout/blockquote is prefixed with `>`, which the row and delimiter
 * tests have to see past. The prefix rides along on the raw lines we copy, so a quoted
 * table is reassembled still quoted.
 */
function unquote(line: string): string {
  return line.replace(/^\s*(?:>\s*)+/, "");
}

/**
 * Loose test: could this line be a table row? Anything non-empty with a pipe in it. The
 * real gate is finding a delimiter row above it — that is what proves it's a table — so
 * this only has to be permissive enough to walk across the rows in between.
 */
function isRow(line: string | undefined): boolean {
  if (line === undefined) return false;
  const text = unquote(line).trim();
  return text.length > 0 && text.includes("|");
}

/**
 * The `| --- | :-: |` row under a table's header. Requiring a pipe keeps a `---` horizontal
 * rule (or a YAML fence) from passing as a single-column delimiter.
 */
function isDelimiter(line: string | undefined): boolean {
  if (line === undefined) return false;
  const text = unquote(line).trim();
  if (!text.includes("|")) return false;
  const cells = text.replace(/^\|/, "").replace(/\|$/, "").split("|");
  return cells.every((cell) => /^\s*:?-+:?\s*$/.test(cell));
}

interface Table {
  headerLine: number;
  delimiterLine: number;
  lastRowLine: number;
}

/**
 * The table containing `line`, or null if it isn't in one. A table lives in a contiguous
 * run of row-ish lines — a blank line or prose without a pipe ends it — and is anchored on
 * the *first* delimiter row in that run, with the line above it as its header.
 *
 * Anchoring on the first delimiter in the whole run, rather than the first one found while
 * walking up from the selection, matters in both directions: a body row of literal dashes
 * (`| --- | --- |`) is itself delimiter-shaped and would otherwise pose as the delimiter,
 * while a paragraph line that happens to contain a pipe, sitting directly above the table
 * with no blank line, would otherwise be absorbed and tested as the header.
 */
function findTable(editor: EditorLines, line: number): Table | null {
  const lastLine = editor.lastLine();
  if (line < 0 || line > lastLine || !isRow(editor.getLine(line))) return null;

  let top = line;
  while (top > 0 && isRow(editor.getLine(top - 1))) top -= 1;
  let lastRowLine = line;
  while (lastRowLine < lastLine && isRow(editor.getLine(lastRowLine + 1))) lastRowLine += 1;

  let delimiterLine = top + 1;
  while (delimiterLine <= lastRowLine && !isDelimiter(editor.getLine(delimiterLine))) delimiterLine += 1;
  if (delimiterLine > lastRowLine) return null;

  const headerLine = delimiterLine - 1;
  if (isDelimiter(editor.getLine(headerLine))) return null;
  // The selection started in whatever preceded the table, not in the table itself.
  if (line < headerLine) return null;

  return { headerLine, delimiterLine, lastRowLine };
}

/**
 * The end of the selection as a line the user actually selected. Dragging downward past a
 * line usually lands on column 0 of the *next* one, which isn't part of the selection.
 */
function lastSelectedLine(editor: EditorLines, from: Pos, to: Pos): Pos {
  if (to.ch === 0 && to.line > from.line) {
    const line = to.line - 1;
    return { line, ch: editor.getLine(line).length };
  }
  return to;
}

/**
 * The selected Markdown, with a partial table selection completed into a real table.
 *
 * Rows that the selection only partly covers are widened to whole lines (half a row would
 * lose cells), and the table's header and delimiter are prepended when the selection starts
 * below them. A selection that isn't in a table is returned untouched.
 */
export function selectionMarkdown(editor: EditorLines, from: Pos, to: Pos): string {
  const table = findTable(editor, from.line);
  if (!table) return editor.getRange(from, to);

  // The start is inside the table by construction, so it always widens. The end may have
  // run out the bottom of it, and widening an unrelated line would send prose the user
  // never selected — so only widen while still in the table.
  const end = lastSelectedLine(editor, from, to);
  const start: Pos = { line: from.line, ch: 0 };
  const stop: Pos =
    end.line <= table.lastRowLine ? { line: end.line, ch: editor.getLine(end.line).length } : end;
  const body = editor.getRange(start, stop);

  const header = editor.getLine(table.headerLine);
  const delimiter = editor.getLine(table.delimiterLine);
  // Supply whichever of the two the selected range is missing.
  if (start.line > table.delimiterLine) return `${header}\n${delimiter}\n${body}`;
  if (start.line === table.delimiterLine) return `${header}\n${body}`;
  if (end.line < table.delimiterLine) return `${body}\n${delimiter}`; // the header row alone
  return body;
}
