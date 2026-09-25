---
name: GroundTruth
description: Every claim sounded against its source, on a quiet sea chart.
colors:
  chart-paper: "#f4f5f9"
  sheet-white: "#ffffff"
  well: "#eceef4"
  hairline: "#dfe2ea"
  hairline-strong: "#bcc2d0"
  ink: "#151a33"
  ink-secondary: "#464d66"
  ink-tertiary: "#666e87"
  harbor: "#172056"
  harbor-raised: "#222c6e"
  harbor-line: "#34408a"
  harbor-ink: "#eef0ff"
  harbor-dim: "#aab2e2"
  verdict-green: "#1c7a4e"
  verdict-green-wash: "#dcf2e5"
  verdict-amber: "#8a5a0b"
  verdict-amber-fill: "#e0a526"
  verdict-amber-wash: "#fbefcf"
  verdict-red: "#b33a2e"
  verdict-red-wash: "#fbe2de"
  act-indigo: "#3f51e0"
  act-indigo-deep: "#2f3fc4"
  act-indigo-wash: "#e4e7ff"
  signal-periwinkle: "#9fb0ff"
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
chart. A harbour-navy masthead carries depth contours, chart-white paper
holds the report, and each claim is marked by whether it touched bottom.

It replaces a "test report" design that read as machine output: uppercase
mono labels, grey on grey, nothing that said what the product was about. The
checkable structure stayed; the voice changed. Labels are sentence case in
plain words, questions and claims are set in a reading serif, and colour comes
from the harbour and the verdicts rather than from rules and boxes.

**Key characteristics:**
- A harbour-navy masthead on every page. On Ask it becomes the hero: a large
  serif line, the question field as the centrepiece, and suggestions as chips
  over faint depth contours.
- Questions, claims and excerpts in Newsreader. Interface in Schibsted
  Grotesk. Monospace only for code and file paths.
- Verdicts drawn by fill (solid, half, hollow) and hue together. The focused
  claim's reason is marked with a highlighter wash in its verdict colour.
- An indigo leader line pins the focused claim to its excerpt.

## Colours

- **Harbour** (#172056) with its raised (#222c6e) and line (#34408a) tones:
  the masthead, the hero, code blocks, and the excerpt marker. Text on it is
  #eef0ff, secondary #aab2e2.
- **Chart paper** (#f4f5f9) under **white sheets**; **well** (#eceef4) for
  tracks; hairlines #dfe2ea / #bcc2d0.
- **Ink** (#151a33, #464d66, #666e87): a navy-tinted ink, so text belongs to
  the harbour rather than to a neutral grey.
- **Verdicts:** green #1c7a4e, amber #8a5a0b (fill #e0a526), red #b33a2e, each
  with a wash used as the highlighter.
- **Action:** indigo #3f51e0 on light surfaces (links to traces, citation
  chips, the leader line, focus). On the harbour the Ask button is periwinkle
  #9fb0ff with harbour text, and focus rings turn periwinkle.

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
- **Code** (Atkinson Hyperlegible Mono): code blocks on harbour navy, inline
  code on an indigo wash, and file paths. Nothing else is mono.

## Layout

The hero spans the full width; content sits in a 1320px container with 32px
sides (16px on phones). At 1024px and up a report is two columns, claims
beside a 400px evidence column that stays in view, 48px apart. Below that it
stacks in reading order. Asking scrolls the reader to the results.

## Components

- **Question field:** a white 56px field and a periwinkle Ask button inside
  one raised harbour tray.
- **Suggestion chips:** full-round, harbour-line border. A note on what the
  question shows is set in serif italic periwinkle.
- **Summary:** "2 of 2 claims are fully supported by their sources", a bar
  with one segment per checked claim, and the verdict counts.
- **Claim row:** the verdict mark, the claim in serif, then "Supported by
  excerpt 1". When focused: the verifier's reason, highlighter-marked.
- **Excerpt:** a numbered harbour disc, the section title, the path in mono,
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
