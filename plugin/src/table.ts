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

interface TableHead {
  headerLine: number;
  delimiterLine: number;
}

/**
 * The header/delimiter pair of the table containing `line`, or null if it isn't in one.
 * Walks upward across row-ish lines; a blank line or ordinary prose ends the table and
 * stops the search.
 */
function findTableHead(editor: EditorLines, line: number): TableHead | null {
  if (line < 0 || line > editor.lastLine()) return null;

  // `line` is itself the header (the delimiter is directly below it).
  if (isRow(editor.getLine(line)) && line < editor.lastLine() && isDelimiter(editor.getLine(line + 1))) {
    return { headerLine: line, delimiterLine: line + 1 };
  }

  let i = line;
  while (i >= 0 && isRow(editor.getLine(i)) && !isDelimiter(editor.getLine(i))) i -= 1;
  if (i < 0 || !isDelimiter(editor.getLine(i))) return null;

  const headerLine = i - 1;
  if (headerLine < 0 || !isRow(editor.getLine(headerLine)) || isDelimiter(editor.getLine(headerLine))) {
    return null;
  }
  return { headerLine, delimiterLine: i };
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
  const table = findTableHead(editor, from.line);
  if (!table) return editor.getRange(from, to);

  const end = lastSelectedLine(editor, from, to);
  const start: Pos = { line: from.line, ch: isRow(editor.getLine(from.line)) ? 0 : from.ch };
  const stop: Pos = isRow(editor.getLine(end.line))
    ? { line: end.line, ch: editor.getLine(end.line).length }
    : end;
  const body = editor.getRange(start, stop);

  // Prepend only what the selection is missing: both rows when it starts in the table body,
  // just the header when it starts on the delimiter, nothing when it starts at the header.
  if (start.line > table.delimiterLine) {
    return `${editor.getLine(table.headerLine)}\n${editor.getLine(table.delimiterLine)}\n${body}`;
  }
  if (start.line === table.delimiterLine) {
    return `${editor.getLine(table.headerLine)}\n${body}`;
  }
  return body;
}
