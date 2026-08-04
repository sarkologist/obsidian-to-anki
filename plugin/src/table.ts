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
 *
 * Two ways to select part of a table, and they need different handling:
 *
 * - In Source mode (or dragging past a table in Live Preview) it's an ordinary text
 *   selection, and the editor's own range says what was picked — `selectionMarkdown`.
 * - Inside a Live Preview table, Obsidian runs its own cell-range selection instead. The
 *   document selection stays in the anchor cell, so the editor reports one row no matter
 *   how many are highlighted; the caller reads the real rectangle out of the rendered
 *   table and asks for it by row/column index — `tableRectangleMarkdown`.
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

const FENCE = /^ {0,3}(`{3,}|~{3,})(.*)$/;

/**
 * Is `line` inside a fenced code block? A note that documents Markdown has table-shaped
 * lines in its fences, and those are text the user meant literally — completing them into a
 * table would send rendered HTML where code was selected. Only a fence of the same
 * character and at least the same length closes one, and a closing fence carries no info
 * string, so ```` ```md ```` inside a ``~~~`` block doesn't end it.
 */
function insideCodeFence(editor: EditorLines, line: number): boolean {
  let open: { char: string; length: number } | null = null;
  for (let i = 0; i < line; i += 1) {
    const match = FENCE.exec(editor.getLine(i));
    if (!match) continue;
    const [, marker, rest] = match;
    if (!open) {
      open = { char: marker[0], length: marker.length };
    } else if (marker[0] === open.char && marker.length >= open.length && rest.trim() === "") {
      open = null;
    }
  }
  return open !== null;
}

export interface Table {
  headerLine: number;
  delimiterLine: number;
  lastRowLine: number;
}

/** How many rows the table renders as: its header, plus every body row. */
export function tableRowCount(table: Table): number {
  return table.lastRowLine - table.headerLine;
}

/** The source line of a rendered row, counting the header as row 0 (as the DOM does). */
function lineOfRow(table: Table, row: number): number {
  return row === 0 ? table.headerLine : table.delimiterLine + row;
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
export function findTable(editor: EditorLines, line: number): Table | null {
  const lastLine = editor.lastLine();
  if (line < 0 || line > lastLine || !isRow(editor.getLine(line))) return null;
  if (insideCodeFence(editor, line)) return null;

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

interface SplitRow {
  /** Any `>` quoting the row carries, so a sliced row stays inside its callout. */
  prefix: string;
  cells: string[];
}

/**
 * A row's cells. GFM splits on every pipe that isn't backslash-escaped — one inside a code
 * span or a math span still separates cells — so this needs no notion of either.
 */
function splitRow(line: string): SplitRow {
  const prefix = /^\s*(?:>\s*)*/.exec(line)?.[0] ?? "";
  const text = line.slice(prefix.length).trim();
  const cells: string[] = [];
  let cell = "";
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (char === "\\" && i + 1 < text.length) {
      cell += char + text[i + 1];
      i += 1;
    } else if (char === "|") {
      cells.push(cell);
      cell = "";
    } else {
      cell += char;
    }
  }
  cells.push(cell);
  // The outer pipes are optional in GFM; where they are present they leave an empty cell.
  if (text.startsWith("|")) cells.shift();
  if (cells.length > 0 && text.endsWith("|") && cells[cells.length - 1].trim() === "") cells.pop();
  return { prefix, cells: cells.map((c) => c.trim()) };
}

/**
 * The rows `first`..`last` of `table` as a table of their own, header and delimiter
 * included, counting the header as row 0. `columns` narrows every row to that span of
 * cells; null keeps each row exactly as written.
 *
 * This is the Live Preview path: Obsidian's own cell-range selection is invisible to the
 * editor's document selection, so the caller reads the rectangle out of the rendered table
 * and names it by index.
 */
export function tableRectangleMarkdown(
  editor: EditorLines,
  table: Table,
  rows: { first: number; last: number },
  columns: { first: number; last: number } | null,
): string {
  const take = (line: number): string => {
    const text = editor.getLine(line);
    if (!columns) return text;
    const { prefix, cells } = splitRow(text);
    return `${prefix}| ${cells.slice(columns.first, columns.last + 1).join(" | ")} |`;
  };

  const out = [take(table.headerLine), take(table.delimiterLine)];
  // Row 0 is the header, already emitted; body rows start at 1.
  for (let row = Math.max(rows.first, 1); row <= rows.last; row += 1) {
    out.push(take(lineOfRow(table, row)));
  }
  return out.join("\n");
}
