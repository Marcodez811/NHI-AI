/**
 * Escape `_` so markdown never turns underscores into emphasis (file names such
 * as `a_b_c.docx` otherwise render italic when CJK punctuation sits nearby).
 * `*` / `**` are untouched. Fenced blocks (including an unterminated one while
 * streaming) and inline code spans are left as-is.
 */
export function escapeUnderscores(text: string): string {
  const lines = text.split("\n");
  let fence: string | null = null;
  return lines
    .map((line) => {
      const match = /^\s{0,3}(`{3,}|~{3,})/.exec(line);
      if (fence) {
        if (match && match[1][0] === fence[0] && match[1].length >= fence.length) fence = null;
        return line;
      }
      if (match) {
        fence = match[1];
        return line;
      }
      return escapeInline(line);
    })
    .join("\n");
}

function escapeInline(line: string): string {
  let out = "";
  let i = 0;
  while (i < line.length) {
    const ch = line[i];
    if (ch === "`") {
      let run = 1;
      while (line[i + run] === "`") run++;
      const ticks = "`".repeat(run);
      const close = line.indexOf(ticks, i + run);
      if (close === -1) {
        out += ticks;
        i += run;
      } else {
        out += line.slice(i, close + run);
        i = close + run;
      }
    } else if (ch === "\\" && i + 1 < line.length) {
      out += line.slice(i, i + 2);
      i += 2;
    } else if (ch === "_") {
      out += "\\_";
      i++;
    } else {
      out += ch;
      i++;
    }
  }
  return out;
}
