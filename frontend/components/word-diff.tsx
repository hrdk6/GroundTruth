/**
 * Word-level difference between the same section in two releases.
 *
 * Conflict notes used to print both versions and leave the reader to spot the
 * change. On a 400-word section that change can be one word -- "beta" became
 * "stable" -- so the page marks it. A longest-common-subsequence over words is
 * plenty at section size (a few hundred words a side).
 */

import { reflow } from "@/components/answer-text";

type Piece = { text: string; changed: boolean };

const MAX_WORDS = 400;

function words(text: string): string[] {
  return text.split(/(\s+)/).filter((token) => token.length > 0);
}

/** For each side, which words are not in the other side's common subsequence. */
export function diffWords(left: string, right: string): { left: Piece[]; right: Piece[] } {
  // Diff the text as the reader sees it: hard wraps are not differences.
  const a = words(reflow(left)).slice(0, MAX_WORDS * 2);
  const b = words(reflow(right)).slice(0, MAX_WORDS * 2);
  const isSpace = (token: string) => /^\s+$/.test(token);
  const aw = a.filter((t) => !isSpace(t));
  const bw = b.filter((t) => !isSpace(t));

  // lcs[i][j]: common subsequence length of aw[i:] and bw[j:].
  const lcs: Uint16Array[] = Array.from({ length: aw.length + 1 }, () => new Uint16Array(bw.length + 1));
  for (let i = aw.length - 1; i >= 0; i--) {
    for (let j = bw.length - 1; j >= 0; j--) {
      lcs[i][j] = aw[i] === bw[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const keepA = new Array<boolean>(aw.length).fill(false);
  const keepB = new Array<boolean>(bw.length).fill(false);
  for (let i = 0, j = 0; i < aw.length && j < bw.length; ) {
    if (aw[i] === bw[j]) {
      keepA[i] = keepB[j] = true;
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) i++;
    else j++;
  }

  const pieces = (tokens: string[], keep: boolean[]): Piece[] => {
    let wordIndex = 0;
    const flags = tokens.map((token) => (isSpace(token) ? false : !keep[wordIndex++]));
    // A space is highlighted only between two changed words on one line, so a
    // changed phrase is one mark -- and a paragraph break never is.
    tokens.forEach((token, i) => {
      if (isSpace(token) && !token.includes("\n")) flags[i] = !!flags[i - 1] && !!flags[i + 1];
    });
    const out: Piece[] = [];
    tokens.forEach((token, i) => {
      const last = out[out.length - 1];
      if (last && last.changed === flags[i]) last.text += token;
      else out.push({ text: token, changed: flags[i] });
    });
    return out;
  };
  return { left: pieces(a, keepA), right: pieces(b, keepB) };
}

export function DiffText({ pieces, tone }: { pieces: Piece[]; tone: "latest" | "other" }) {
  const wash = tone === "latest" ? "var(--color-pass-wash)" : "var(--color-fail-wash)";
  const ink = tone === "latest" ? "var(--color-pass)" : "var(--color-fail)";
  return (
    <>
      {pieces.map((piece, index) =>
        piece.changed ? (
          <mark
            key={index}
            className="rounded-[3px] px-[0.15em] box-decoration-clone"
            style={{ backgroundColor: wash, color: ink }}
          >
            <Inline text={piece.text} />
          </mark>
        ) : (
          <span key={index}>
            <Inline text={piece.text} />
          </span>
        ),
      )}
    </>
  );
}

/** Inline code inside a diff piece; an unbalanced backtick stays as typed. */
function Inline({ text }: { text: string }) {
  return (
    <>
      {text.split(/(`[^`\n]+`)/).map((part, index) =>
        part.length > 2 && part.startsWith("`") && part.endsWith("`") ? (
          <code key={index} className="mono text-[0.86em] px-[0.25em] rounded-[4px] bg-well/80">
            {part.slice(1, -1)}
          </code>
        ) : (
          part
        ),
      )}
    </>
  );
}
