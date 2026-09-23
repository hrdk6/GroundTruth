---
name: GroundTruth
description: Answers you can check, set as a test report.
colors:
  cool-paper: "#f3f4f1"
  report-white: "#ffffff"
  well-gray: "#eaece7"
  hairline: "#dadcd6"
  hairline-strong: "#b8bcb3"
  report-ink: "#15171c"
  ink-secondary: "#454a54"
  ink-tertiary: "#666c78"
  verdict-green: "#1e7a4c"
  verdict-green-wash: "#e2f0e7"
  verdict-amber: "#8f5b0f"
  verdict-amber-fill: "#d69e2e"
  verdict-amber-wash: "#f6ecd6"
  verdict-red: "#b8322c"
  verdict-red-wash: "#f8e1de"
  ask-ultramarine: "#2b44ff"
  ask-ultramarine-deep: "#1c30d6"
  ask-ultramarine-wash: "#e6e9ff"
typography:
  display:
    fontFamily: "Atkinson Hyperlegible Next, ui-sans-serif, system-ui, sans-serif"
    fontSize: "34px"
    fontWeight: 800
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "Atkinson Hyperlegible Next, ui-sans-serif, system-ui, sans-serif"
    fontSize: "20px"
    fontWeight: 800
    lineHeight: 1.3
  title:
    fontFamily: "Atkinson Hyperlegible Next, ui-sans-serif, system-ui, sans-serif"
    fontSize: "18px"
    fontWeight: 400
    lineHeight: 1.65
  body:
    fontFamily: "Atkinson Hyperlegible Next, ui-sans-serif, system-ui, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.6
  label:
    fontFamily: "Atkinson Hyperlegible Mono, ui-monospace, SF Mono, Menlo, monospace"
    fontSize: "11px"
    fontWeight: 700
    letterSpacing: "0.06em"
  data:
    fontFamily: "Atkinson Hyperlegible Mono, ui-monospace, SF Mono, Menlo, monospace"
    fontSize: "13px"
    fontWeight: 400
    fontFeature: "tnum"
rounded:
  chip: "4px"
  sheet: "6px"
  pill: "9999px"
spacing:
  row-x: "24px"
  row-y: "20px"
  gutter: "48px"
  page-x: "32px"
  page-x-phone: "16px"
components:
  button-ask:
    backgroundColor: "{colors.ask-ultramarine}"
    textColor: "{colors.report-white}"
    rounded: "{rounded.sheet}"
    height: "56px"
    padding: "0 32px"
  button-ask-hover:
    backgroundColor: "{colors.ask-ultramarine-deep}"
  button-secondary:
    backgroundColor: "{colors.report-white}"
    textColor: "{colors.report-ink}"
    rounded: "{rounded.sheet}"
    padding: "8px 14px"
  input-question:
    backgroundColor: "{colors.report-white}"
    textColor: "{colors.report-ink}"
    rounded: "{rounded.sheet}"
    height: "56px"
    padding: "0 16px"
  citation-chip:
    backgroundColor: "{colors.report-white}"
    textColor: "{colors.report-ink}"
    typography: "{typography.data}"
    rounded: "{rounded.chip}"
    padding: "1px 6px"
  sheet:
    backgroundColor: "{colors.report-white}"
    rounded: "{rounded.sheet}"
---

# Design System: GroundTruth

## Overview

**Creative North Star: "The Test Report"**

An answer is a test run. Each claim is a case, judged supported, partly supported, or unsupported against the excerpt it cites, and the page reads like a well-set test report: a summary line of counts, ruled result rows on white sheets, identifiers and timings in a mono machine voice. It is written for a skeptical reviewer at a desk in daylight who opened a link from a README and wants to check the system rather than trust it.

The report is set large and airy, never as a log. Legibility is the brief: the text face was drawn for low-vision readers, claims are 18px at a 1.65 leading, and every surface keeps one column of prose under 75 characters. Density lives in the data columns, not in the reading.

The world refuses the chat stream, the chat bubble, and the dark console. There is one colour for action and it is spent only on asking.

**Key Characteristics:**
- Cool paper ground under white report sheets with a single hairline and a soft offset shadow.
- Verdicts drawn by fill (solid, half, hollow) first and hue second.
- Ruled rows instead of card grids; one gutter rhythm shared by every page.
- Ultramarine for Ask and keyboard focus, nothing else.
- A drawn leader line pins the focused claim to its evidence.

## Colors

A restrained report palette: cool neutrals carry the page, a verdict triad carries judgement, and one saturated blue carries action.

### Primary
- **Ask Ultramarine** (#2b44ff): the Ask button, keyboard focus rings, the input's focus border, and the selection wash (#e6e9ff). Its deep variant (#1c30d6) is Ask's hover.

### Secondary
- **Verdict Green** (#1e7a4c): a supported claim's mark and verdict word; the "answered from" side of a version diff, on its wash (#e2f0e7).
- **Verdict Amber** (#8f5b0f text, #d69e2e fill): a partly supported claim; the fill is the half of the half-filled mark.
- **Verdict Red** (#b8322c): unsupported claims, failed stages, demoted ranks, errors, and the side of a version diff that was not used, on its wash (#f8e1de).

### Neutral
- **Cool Paper** (#f3f4f1): the page ground, and the focused or hovered row inside a sheet.
- **Report White** (#ffffff): every sheet: claim lists, evidence, tables, suggestions.
- **Well Gray** (#eaece7): code blocks, inline code, meter and bar tracks.
- **Hairline** (#dadcd6) and **Hairline Strong** (#b8bcb3): row rules and sheet borders; control borders and chip outlines.
- **Report Ink** (#15171c), **Ink Secondary** (#454a54), **Ink Tertiary** (#666c78): primary text, supporting text, and labels. Tertiary is the floor: it holds 4.8:1 on Cool Paper.

### Named Rules
**The One Blue Rule.** Ultramarine marks the single action that commits, asking, and keyboard focus. Nothing else on any page is blue, including links, which are underlined ink.

**The Earned Colour Rule.** A difference between two runs is green or red only when its paired interval excludes zero. A difference that is noise is set in Ink Secondary, because a coloured delta is a claim.

## Typography

**Display Font:** Atkinson Hyperlegible Next (with ui-sans-serif, system-ui)
**Body Font:** Atkinson Hyperlegible Next
**Label/Mono Font:** Atkinson Hyperlegible Mono

**Character:** A legibility-first family from the Braille Institute. Its letterforms cannot be mistaken for each other (I, l, 1; O, 0), which is the whole job of a page read closely for evidence. The mono sibling keeps identifiers and numbers in the same voice.

### Hierarchy
- **Display** (800, 34px, 28px on phones, 1.2, -0.02em): the question at the top of a report, and page titles.
- **Headline** (800, 20px, 1.3): section heads: Evidence, the version difference, Rank trail.
- **Title** (400, 18px, 1.65): the text of a claim, the page's main reading.
- **Body** (400, 16px, 1.6; 15px for supporting copy): ledes, excerpts, explanations, kept under 75ch.
- **Label** (Mono 700, 11px, 0.06em, uppercase): the machine voice: verdict words, case ids, version headers.
- **Data** (Mono 400, 13px, tabular numerals): identifiers, paths, timings, metrics, commit ids.

### Named Rules
**The Machine Voice Rule.** Mono is for things a machine produced or reads: code, paths, ids, measurements, verdict codes. A sentence is never set in mono, even inside a details row.

## Layout

Pages sit in a 1320px container with 32px side padding (16px on phones). At 1024px and up every report surface shares one two-column grid: a fluid main column and a 400px column, 48px apart. On Ask it runs the whole page (the question over the claims, the suggestions over the evidence), so the first viewport shows the recorded run's first claim already pinned to its excerpt. Below 1024px everything stacks in reading order: claims, then evidence, then the version difference.

Rows breathe: 20px vertical and 24px horizontal inside sheets, more space above a heading than below it. Tables scroll inside their own sheet rather than widening the page.

## Elevation & Depth

Depth is tonal first: Cool Paper ground, white sheets, Well Gray insets. Sheets add one hairline border and a soft two-part shadow with real offset (`0 1px 2px rgb(21 23 28 / 0.04), 0 6px 18px -8px rgb(21 23 28 / 0.1)`). Nothing floats higher than a sheet; the sticky masthead uses a translucent paper fill with a light backdrop blur only so content scrolling under it stays legible.

### Named Rules
**The One Sheet Rule.** A sheet never sits inside another sheet. Grouping inside a sheet is done with hairline rules.

## Shapes

Quietly squared: 6px corners on sheets, buttons and inputs, 4px on chips and inline code, full rounding only on meter tracks and the stage line. Verdict marks are 12–14px squares with a 1.5px stroke and 1.5px corners; the solid, half, and hollow fills are the vocabulary.

## Components

### Buttons
- **Shape:** gently squared (6px).
- **Ask:** Ask Ultramarine with white 17px bold text, 56px tall, 32px horizontal padding. Never disabled for an empty question: pressing it focuses the input instead.
- **Hover / Focus:** hover deepens to #1c30d6; focus is the global 2px ultramarine ring, 2px offset.
- **Secondary:** Report White with a Hairline Strong border and ink bold text ("Ask it live"); the border darkens to ink on hover.

### Chips
- **Citation chip:** mono 12px number in a 4px-rounded white box with a Hairline Strong border, raised slightly off the baseline; hover darkens the border. In the evidence column the marker inverts: white on Report Ink.

### Cards / Containers
- **Corner Style:** 6px.
- **Background:** Report White on Cool Paper.
- **Shadow Strategy:** the one sheet shadow (Elevation & Depth).
- **Border:** one Hairline.
- **Internal Padding:** 20px by 24px rows, divided by hairlines.

### Inputs / Fields
- **Style:** white field, Hairline Strong border, 6px corners, 56px tall, 18px text.
- **Focus:** border turns ultramarine with a 4px ultramarine-wash ring.
- **Selects:** white, Hairline Strong border, mono 13px values.

### Navigation
- **Style:** Ask, Traces, Experiments at 15px in Ink Secondary; the current page is bold ink with a 3px ink tab sitting on the masthead's bottom rule. The wordmark (the three verdict marks, then "GroundTruth" in 800) drops its name below 440px.

### Claim row (signature)
A verdict mark in an 18px gutter, the claim at Title size, then a machine-voice footer: `CASE 01 · SUPPORTED · cites [1]`. The focused row turns Cool Paper and shows the verifier's own reason.

### Leader line (signature)
When the columns sit side by side, a 1.25px ink elbow runs from the focused claim, across the 48px gutter, to the excerpt it cites: a solid dot at the claim, a hollow one at the evidence. It draws in over 480ms with an exponential ease-out and redraws whenever the focus changes.

### Stage line
The wait for a cold answer: one unbroken 6px track under the pipeline's route, with an ink cursor that loops along it, plus an honest elapsed counter. It never claims to know which stage is running.

## Do's and Don'ts

### Do:
- **Do** draw every verdict by fill and hue together: solid green, half amber, hollow red.
- **Do** spend Ask Ultramarine only on Ask and focus (The One Blue Rule).
- **Do** keep claims at 18px and supporting prose under 75ch.
- **Do** set identifiers, paths, timings and metrics in Atkinson Hyperlegible Mono with tabular numerals.
- **Do** reflow source Markdown's hard wraps before showing an excerpt, and mark exactly which words differ between releases.

### Don't:
- **Don't** render answers as chat bubbles or a message stream.
- **Don't** colour a difference that is not distinguishable from noise (The Earned Colour Rule).
- **Don't** put a coloured stripe on the side of a card, alert or row; alerts take a full hairline in their verdict colour on its wash.
- **Don't** put a small label above a heading; the heading carries itself.
- **Don't** show a number the API or `experiments/` did not produce.
