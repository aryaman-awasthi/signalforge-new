# The design system

`tokens.css` is the only place this product is themed. Every colour, size, space
and shadow on every screen resolves to a token in that file, `tailwind.config.ts`
exposes them by name and holds no hex of its own, and no component in `app/` or
`features/` may use a raw Tailwind palette class (`bg-red-500` and the like).

Everything here was extracted from the Claude Design export, not invented. The
sources, in order of authority:

1. `_ds/modernist-fd1bce54-.../styles.css` — the design system's own token file.
2. `SignalForge Prototype.dc.html` — the 23 screens, which hard-code six colours
   the system has no role for.
3. `ref/*.png` — the rendered truth. Where markup and render disagreed, the
   render won.

The full extraction, including the exact recipe for every component, is
`playbook/design/tokens-extracted.md`. This file is the short version: what each
token is, and which part of the prototype it came from.

## Colour

| Token | Value | Where it comes from, and what it is for |
|---|---|---|
| `--sf-surface` | `#f3f2f2` | `--color-bg`. The page ground. |
| `--sf-surface-raised` | `#eae9e9` | `--color-surface`. Card and input ground — **darker than the page**. Cards in this design sit *in* the ground rather than on top of it, which is why they need almost no shadow. |
| `--sf-ink` | `#201e1d` | `--color-text`. |
| `--sf-ink-muted` | 55% ink | `.text-muted`. Secondary body. |
| `--sf-ink-soft` | 60% ink | Table headers and micro-labels. |
| `--sf-line` | `--color-divider` | Every 1px rule. |
| `--sf-line-strong` | — | The 2px section rule, which reads darker at that weight. |
| `--sf-accent` | `#ec3013` | `--color-accent`. Brand, P0, and the current decision. |
| `--sf-accent-strong` | `#ae1800` | `--color-accent-700`. Accent ink on a light ground. |

### The six the prototype hard-codes

The design system ships a neutral ramp and one accent. The prototype needed
semantic states it has no role for, and wrote them as hex. They are tokens here
so they stop being hex in components.

| Token | Hex | Fill | Deep ink | Means |
|---|---|---|---|---|
| `--sf-ok` | `#1E7A44` | `--sf-ok-bg` `#E7F0E9` | — | verified, complete, joined, pass, kept, recovered |
| `--sf-warn` | `#A8730A` | `--sf-warn-bg` `#F5EAD3` | `--sf-warn-ink` `#7a5406` | uncertain, degraded, paged, watch, partial, paused |
| `--sf-info` | `#1E5FA8` | `--sf-info-bg` `#E3EAF4` | — | acked, read-only, added, commitment, live update |
| `--sf-conflict` | `#C05616` | `--sf-conflict-bg` `#F7E5D8` | `--sf-conflict-ink` `#8a3c0c` | contradicted, timeout, proposed-not-verified, P2 |
| `--sf-closed` | `#6E6A66` | `--sf-closed-bg` `#EBEAE9` | `--sf-closed-ink` `#524F4C` | a closed incident |
| `--sf-pattern-conflict` | 45° stripes | — | — | two sources disagree about this |

Caution and conflict carry a separate deep ink because the mid tone fails
contrast on its own fill. Good and info do not.

`--sf-danger` is `--sf-accent`. **There is exactly one red in this design**, and
severity does not get a traffic light of its own.

### Ramps

`--sf-neutral-100..900` and `--sf-accent-100..900`, both quoted from the design
system, both generated in OKLCH on one shared lightness scale so the same step of
either matches the other in visual value. How the prototype spends them:

- `100` hover ground · `200` the empty track of every bar · `400` somebody who
  has not joined · `500` inert numerals and "no change" dashes · `600`
  micro-label ink and neutral badge borders · `700` neutral badge ink · `800`
  avatar grounds and the asserted-badge ground · `900` the dialog scrim at 50%.

### Severity and evidence

`--sf-sev-p0..p3` and `--sf-evidence-observed / -authoritative / -stated /
-declared`. P0 is the only filled severity. The evidence tokens carry the ink;
the border style carries the rest of the meaning (see below).

## Type

Archivo, weights 400 / 600 / 800, **self-hosted** from
`web/public/fonts/archivo-variable-latin.woff2` and loaded with
`next/font/local` in `app/layout.tsx`. One variable file carries the whole
100–900 axis, which is what Google serves for all three weights anyway.

It is not loaded from Google Fonts, and must not be: the machine this is
demonstrated on may have no outbound network at build time, and a failed font
fetch fails `next build` outright.

| Token | Size | Role |
|---|---|---|
| `--sf-text-hero` | 44px | the one number a panel is about |
| `--sf-text-h1..h6` | 42 / 32 / 25 / 20 / 16 / 13 | quoted from the system's `h1..h6` |
| `--sf-text-body` | 15px | body, `line-height: 1.55` |
| `--sf-text-dense` | 13px | dense body inside panels, table cells |
| `--sf-text-small` | 12px | secondary body, transcripts, checkpoints |
| `--sf-text-micro` | 11px | large pill label |
| `--sf-text-tiny` | 10px | standard pill label, panel micro-labels |
| `--sf-text-nano` | 9px | inline badge, column caption |

`h6` is not a heading in this design: it is the uppercase eyebrow, which is why
it is the one size that *gains* letter-spacing (`--sf-tracking-label`, `0.08em`)
while the display sizes lose it (`--sf-tracking-display`, `-0.015em`).

Panel headers are not `h*` either. They are 11px / `0.1em` / 800 / uppercase —
`--sf-tracking-panel`.

`--sf-font-mono` is used wherever an identifier, a timestamp or a machine value
appears. The design system does not declare it; the prototype repeats it inline
on every one of them, so it is a token here.

## Space, shape, elevation

`--sf-space-1..8` are 4, 8, 12, 16, 24, 32. There is no 5 and no 7.

**Every radius is 0.** All three steps of the export are `0px`; nothing in this
design is rounded. `--sf-radius-card` is kept as a name because it is used across
the app, and its value is `0px`.

Three shadows (`sm` / `md` / `lg`), ink-tinted and soft, used sparingly.
Hierarchy in this design is carried by border weight, not by shadow:

| Border | Means |
|---|---|
| `2px` divider | a section boundary — under the incident header, under a panel header |
| `1px` divider | a row rule inside a list |
| `4px` role colour on `border-top` | **this card is about that state** — accent = the current decision, good = pass, caution = partial, conflict = a contradiction |

## Motion

One keyframe, `sf-pulse`, and one rule for reading it:

- a pulsing **dot** means data is moving;
- a pulsing **pill** means somebody owes an answer.

Nothing else in this product animates. Under `prefers-reduced-motion` everything
holds still and the word beside the indicator carries the meaning.

## The four grammars

This is the part worth learning, because it is what lets a reader understand a
badge before reading it. It is enforced by `lib/ui/primitives.tsx`, where the
whole badge vocabulary is a closed set:

| Fill | Means |
|---|---|
| solid | the platform asserts this |
| border only | somebody claimed it |
| dashed | it was inferred, not measured |
| 45° stripes | two sources disagree about it |

## Components

`web/lib/ui/primitives.tsx` — pills, state pills, severity pills, the badge
vocabulary, evidence chips, confidence bar, delta, sparkline, processing
indicator, card, panel header, kicker, empty state, copy field, avatar.

`web/lib/ui/patterns.tsx` — plan diff rows, ledger step and its marker, layer
verdict tile, ack state pill, escalation countdown, fact grid, button.

Features import from these. A pill written inline in a feature is a bug: it means
a claim about what the product knows was made somewhere the vocabulary does not
reach.
