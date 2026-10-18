/**
 * CSV for spreadsheets (RFC 4180): a cell with a comma, quote or line break is quoted. A cell that starts the way a
 * formula does (=, +, @, a tab or a carriage return) gets a leading apostrophe, so a title someone else wrote can't run
 * as a formula when the file is opened.
 */
export function csvCell(v: string): string {
  const safe = /^[=+@\t\r]/.test(v) ? `'${v}` : v;
  return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

/** Rows of cells as one CSV file's text, ending with a line break. */
export function toCsvText(rows: string[][]): string {
  return rows.map((r) => r.map(csvCell).join(",")).join("\n") + "\n";
}

/** Hand text to the browser as a file to save. */
export function saveText(text: string, filename: string, type = "text/csv;charset=utf-8"): void {
  const href = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = href;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 10_000);
}
