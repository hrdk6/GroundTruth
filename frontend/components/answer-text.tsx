import type { ReactNode } from "react";

/**
 * The part of Markdown the answering model actually writes -- fenced code
 * blocks, inline code, bold -- with citation markers handed to the caller.
 *
 * Deliberately not a Markdown parser: an answer is a handful of sentences,
 * and anything outside this subset renders as the text it is, which is always
 * readable. Rendering it raw was not: a YAML example arrived as one line of
 * literal backticks.
 */
const FENCE = /```[\w-]*[^\S\n]*\n?([\s\S]*?)```/g;

/**
 * Source Markdown with its hard wraps undone.
 *
 * The docs are wrapped at about 80 columns, so an excerpt shown as stored
 * breaks mid-sentence every line -- ragged and tiring to read. Lines inside a
 * paragraph are joined; blank-line paragraphs, list items, headings, tables and
 * fenced code keep their breaks. Only whitespace changes, never a word.
 */
export function reflow(text: string): string {
  const out: string[] = [];
  let inFence = false;
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (trimmed.startsWith("```")) {
      inFence = !inFence;
      out.push(line);
      continue;
    }
    if (inFence) {
      out.push(line);
      continue;
    }
    const previous = out[out.length - 1];
    const startsBlock = /^([*+-]|\d+[.)])\s/.test(trimmed) || /^[#|>]/.test(trimmed);
    const joinable =
      previous !== undefined &&
      previous.trim() !== "" &&
      !previous.trim().startsWith("```") &&
      trimmed !== "" &&
      !startsBlock;
    if (joinable) out[out.length - 1] = `${previous.replace(/\s+$/, "")} ${trimmed}`;
    else out.push(trimmed === "" ? "" : startsBlock ? trimmed : line);
  }
  return out.join("\n").replace(/\n{3,}/g, "\n\n");
}
const INLINE = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\[\d+\])/g;

export function AnswerText({
  text,
  citation,
}: {
  text: string;
  citation: (marker: number, key: string) => ReactNode;
}) {
  const blocks: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(FENCE)) {
    const start = match.index ?? 0;
    let before = text.slice(last, start).replace(/\s+$/, "");
    if (last > 0) before = before.replace(/^\s+/, "");
    if (before) blocks.push(inline(before, `t${start}`, citation));
    blocks.push(
      <pre
        key={`c${start}`}
        className="my-3 overflow-x-auto rounded-md border border-rule bg-well px-4 py-3 mono text-[13.5px] leading-[1.65] text-ink"
      >
        <code>{match[1].replace(/\s+$/, "")}</code>
      </pre>,
    );
    last = start + match[0].length;
  }
  // Whitespace hugging a fence is layout, not content: the block has margins.
  const after = last > 0 ? text.slice(last).replace(/^\s+/, "") : text.slice(last);
  if (after) blocks.push(inline(after, `t${last}`, citation));
  return <>{blocks}</>;
}

function inline(
  text: string,
  key: string,
  citation: (marker: number, key: string) => ReactNode,
): ReactNode {
  return (
    <span key={key}>
      {text.split(INLINE).map((part, index) => {
        const partKey = `${key}-${index}`;
        const marker = part.match(/^\[(\d+)\]$/);
        if (marker) return citation(Number(marker[1]), partKey);
        if (part.length > 2 && part.startsWith("`") && part.endsWith("`")) {
          return (
            <code
              key={partKey}
              // Smaller on a phone, so a 30-character identifier still fits
              // the measure instead of breaking mid-word.
              className="mono text-[0.78em] sm:text-[0.86em] px-[0.3em] py-[0.08em] rounded-[4px] bg-well text-ink"
            >
              {part.slice(1, -1)}
            </code>
          );
        }
        if (part.length > 4 && part.startsWith("**") && part.endsWith("**")) {
          return (
            <strong key={partKey} className="font-bold text-ink">
              {part.slice(2, -2)}
            </strong>
          );
        }
        return part;
      })}
    </span>
  );
}
