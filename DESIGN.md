---
name: GroundTruth
description: Every claim sounded against its source, on a quiet sea chart.
colors:
  chart-paper: "#f5f8fa"
  sheet-white: "#ffffff"
  well: "#edf2f6"
  hairline: "#dde5ec"
  hairline-strong: "#b9c6d2"
  ink: "#17293b"
  ink-secondary: "#45586b"
  ink-tertiary: "#5c6d80"
  shallows: "#e3eef5"
  shallows-raised: "#f5f9fc"
  contour: "#bdd3e1"
  shallows-text-secondary: "#4a6177"
  verdict-green: "#1c7a4e"
  verdict-green-wash: "#dcf2e5"
  verdict-amber: "#8a5a0b"
  verdict-amber-fill: "#e0a526"
  verdict-amber-wash: "#fbefcf"
  verdict-red: "#b33a2e"
  verdict-red-wash: "#fbe2de"
  sea-blue: "#2d6a9f"
  sea-blue-deep: "#245a88"
  sea-blue-wash: "#e2edf6"
typography:
  display:
    fontFamily: "Newsreader, ui-serif, Georgia, serif"
    fontSize: "60px (40px on phones)"
    fontWeight: 500
    lineHeight: 1.02
    letterSpacing: "-0.025em"
  question:
    fontFamily: "Newsreader"
    fontSize: "42px (32px on phones)"
    fontWeight: 500
    lineHeight: 1.12
  claim:
    fontFamily: "Newsreader"
    fontSize: "20px"
    fontWeight: 400
    lineHeight: 1.65
  interface:
    fontFamily: "Schibsted Grotesk, ui-sans-serif, system-ui, sans-serif"
    fontSize: "15-17px"
    fontWeight: "400 / 600"
  label:
    fontFamily: "Schibsted Grotesk"
    fontSize: "13px"
    fontWeight: 500
  code:
    fontFamily: "Atkinson Hyperlegible Mono, ui-monospace, monospace"
    fontSize: "13.5px, or 0.8em inline"
rounded:
  chip: "9999px"
  control: "12px"
  sheet: "10px"
---

# Design System: GroundTruth

## Overview

**Creative North Star: "The Sounding"**

A ground truth is what a sailor finds by sounding: a weighted line dropped
until it touches the bottom. Kubernetes is Greek for helmsman. The product
drops every claim of an answer against its source, so the page is a quiet sea
chart, coloured the way real charts are: pale shallow-water blue with depth
contours for the masthead and hero, chart-white paper for the report, and one
calm sea-blue for action. Each claim is marked by whether it touched bottom.

A first version set the masthead in deep harbour navy; it was too dark to sit
in front of for long, and the palette went light throughout.

It replaces a "test report" design that read as machine output: uppercase
mono labels, grey on grey, nothing that said what the product was about. The
checkable structure stayed; the voice changed. Labels are sentence case in
plain words, questions and claims are set in a reading serif, and colour comes
from the harbour and the verdicts rather than from rules and boxes.

**Key characteristics:**
- A pale shallow-water masthead on every page. On Ask it becomes the hero: a large
  serif line, the question field as the centrepiece, and suggestions as chips
  over faint depth contours.
- Questions, claims and excerpts in Newsreader. Interface in Schibsted
  Grotesk. Monospace only for code and file paths.
- Verdicts drawn by fill (solid, half, hollow) and hue together. The focused
  claim's reason is marked with a highlighter wash in its verdict colour.
- A sea-blue leader line pins the focused claim to its excerpt.

## Colours

- **Shallows** (#e3eef5), raised #f5f9fc, contours #bdd3e1: the masthead and
  hero. Secondary text on it is #4a6177.
- **Chart paper** (#f5f8fa) under **white sheets**; **well** (#edf2f6) for code
  blocks and tracks; hairlines #dde5ec / #b9c6d2.
- **Ink** (#17293b, #45586b, #5c6d80): deep sea-slate, dark enough to read and
  never pure black. Tertiary holds 5.0:1 on chart paper; every text pair passes WCAG AA.
- **Verdicts:** green #1c7a4e, amber #8a5a0b (fill #e0a526), red #b33a2e, each
  with a wash used as the highlighter.
- **Sea-blue** (#2d6a9f, hover #245a88, wash #e2edf6): the Ask button (white
  text), citation markers, links to traces, the leader line, and focus. The
  only saturated colour besides the verdicts.

**The Earned Colour Rule** still holds: a difference between two runs is
green or red only when its paired interval excludes zero.

## Typography

- **Display** (Newsreader 500, 60/40px, -0.025em): the hero line and page
  titles.
- **Question** (Newsreader 500, 42/32px): the question heading a report.
- **Claim** (Newsreader 400, 20px, 1.65): the main reading. Excerpts use the
  same face at 16.5px.
- **Interface** (Schibsted Grotesk): body copy, controls, navigation.
- **Label** (Schibsted Grotesk 500, 13px, sentence case): verdict words,
  version headers. Never uppercase, never tracked out.
- **Code** (Atkinson Hyperlegible Mono): code blocks on the well, inline
  code on a sea-blue wash, and file paths. Nothing else is mono.

## Layout

The hero spans the full width; content sits in a 1320px container with 32px
sides (16px on phones). At 1024px and up a report is two columns, claims
beside a 400px evidence column that stays in view, 48px apart. Below that it
stacks in reading order. Asking scrolls the reader to the results.

## Components

- **Question field:** a white 56px field and a sea-blue Ask button inside
  one raised tray on the shallows.
- **Suggestion chips:** full-round, harbour-line border. A note on what the
  question shows is set in serif italic sea-blue.
- **Summary:** "2 of 2 claims are fully supported by their sources", a bar
  with one segment per checked claim, and the verdict counts.
- **Claim row:** the verdict mark, the claim in serif, then "Supported by
  excerpt 1". When focused: the verifier's reason, highlighter-marked.
- **Excerpt:** a numbered sea-blue disc, the section title, the path in mono,
  and the excerpt in serif.
- **Sounding line (the wait):** a lead drops on a dashed line to a green
  seabed and is hauled back, on a loop, beside the question, the route in
  plain words, and an honest elapsed counter. It never claims to know which
  stage is running.

## Do's and Don'ts

**Do**
- Draw every verdict by fill and hue together.
- Keep claims at 20px serif and prose under 75ch.
- Clip decoration with `overflow: clip`, never `hidden`: a hidden overflow is
  a scroll container, and focusing a chip once scrolled the hero out from
  under itself.

**Don't**
- Set labels in uppercase or monospace.
- Put a small label above a heading.
- Colour a difference that is noise.
- Show a number the API or `experiments/` did not produce. The contours carry
  no soundings for that reason: a depth figure would read as data.
