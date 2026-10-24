// Tiny, safe Markdown for AI summaries: paragraphs, bullet lists, `code` and **bold**. Everything is escaped first.
import { esc } from "./format";

export function md(text: string): string {
  const inline = (t: string) => esc(t).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  const out: string[] = []; let list: string[] | null = null;
  for (const raw of String(text || "").split("\n")) {
    const line = raw.trimEnd(), li = line.match(/^\s*[-*]\s+(.*)/);
    if (li) { (list ||= []).push(`<li>${inline(li[1])}</li>`); continue; }
    if (list) { out.push(`<ul>${list.join("")}</ul>`); list = null; }
    if (line.trim()) out.push(`<p>${inline(line.replace(/^#+\s+/, ""))}</p>`);
  }
  if (list) out.push(`<ul>${list.join("")}</ul>`);
  return out.join("");
}
