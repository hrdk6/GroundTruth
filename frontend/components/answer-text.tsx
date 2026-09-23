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
    if (start > last) blocks.push(inline(text.slice(last, start), `t${start}`, citation));
    blocks.push(
      <pre
        key={`c${start}`}
        className="my-2 overflow-x-auto rounded-sm border border-line bg-panel px-3 py-2 mono text-[12px] leading-relaxed text-text"
      >
        <code>{match[1].replace(/\s+$/, "")}</code>
      </pre>,
    );
    last = start + match[0].length;
  }
  if (last < text.length) blocks.push(inline(text.slice(last), `t${last}`, citation));
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
              className="mono text-[0.85em] px-1 py-px rounded-[2px] bg-raised text-bright"
            >
              {part.slice(1, -1)}
            </code>
          );
        }
        if (part.length > 4 && part.startsWith("**") && part.endsWith("**")) {
          return (
            <strong key={partKey} className="font-semibold text-bright">
              {part.slice(2, -2)}
            </strong>
          );
        }
        return part;
      })}
    </span>
  );
}
