import type { Config } from "tailwindcss";

// Every value here resolves to a design-system token, so the palette, the type
// scale and the spacing are themed in exactly one place: design-system/tokens.css.
// There is no hex in this file and there must never be one - a colour that only
// exists in the Tailwind config is a colour the design system does not know about.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./features/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: "rgb(var(--sf-surface) / <alpha-value>)",
        "surface-raised": "rgb(var(--sf-surface-raised) / <alpha-value>)",
        ink: "rgb(var(--sf-ink) / <alpha-value>)",
        "ink-muted": "rgb(var(--sf-ink-muted) / <alpha-value>)",
        "ink-soft": "rgb(var(--sf-ink-soft) / <alpha-value>)",
        accent: "rgb(var(--sf-accent) / <alpha-value>)",
        "accent-strong": "rgb(var(--sf-accent-strong) / <alpha-value>)",
        ok: "rgb(var(--sf-ok) / <alpha-value>)",
        "ok-bg": "rgb(var(--sf-ok-bg) / <alpha-value>)",
        warn: "rgb(var(--sf-warn) / <alpha-value>)",
        "warn-bg": "rgb(var(--sf-warn-bg) / <alpha-value>)",
        "warn-ink": "rgb(var(--sf-warn-ink) / <alpha-value>)",
        danger: "rgb(var(--sf-danger) / <alpha-value>)",
        info: "rgb(var(--sf-info) / <alpha-value>)",
        "info-bg": "rgb(var(--sf-info-bg) / <alpha-value>)",
        conflict: "rgb(var(--sf-conflict) / <alpha-value>)",
        "conflict-bg": "rgb(var(--sf-conflict-bg) / <alpha-value>)",
        "conflict-ink": "rgb(var(--sf-conflict-ink) / <alpha-value>)",
        closed: "rgb(var(--sf-closed) / <alpha-value>)",
        "closed-bg": "rgb(var(--sf-closed-bg) / <alpha-value>)",
        "closed-ink": "rgb(var(--sf-closed-ink) / <alpha-value>)",
        line: "rgb(var(--sf-line) / <alpha-value>)",
        "line-strong": "rgb(var(--sf-line-strong) / <alpha-value>)",
        neutral: {
          100: "rgb(var(--sf-neutral-100) / <alpha-value>)",
          200: "rgb(var(--sf-neutral-200) / <alpha-value>)",
          300: "rgb(var(--sf-neutral-300) / <alpha-value>)",
          400: "rgb(var(--sf-neutral-400) / <alpha-value>)",
          500: "rgb(var(--sf-neutral-500) / <alpha-value>)",
          600: "rgb(var(--sf-neutral-600) / <alpha-value>)",
          700: "rgb(var(--sf-neutral-700) / <alpha-value>)",
          800: "rgb(var(--sf-neutral-800) / <alpha-value>)",
          900: "rgb(var(--sf-neutral-900) / <alpha-value>)",
        },
        brand: {
          100: "rgb(var(--sf-accent-100) / <alpha-value>)",
          200: "rgb(var(--sf-accent-200) / <alpha-value>)",
          300: "rgb(var(--sf-accent-300) / <alpha-value>)",
          400: "rgb(var(--sf-accent-400) / <alpha-value>)",
          500: "rgb(var(--sf-accent-500) / <alpha-value>)",
          600: "rgb(var(--sf-accent-600) / <alpha-value>)",
          700: "rgb(var(--sf-accent-700) / <alpha-value>)",
          800: "rgb(var(--sf-accent-800) / <alpha-value>)",
          900: "rgb(var(--sf-accent-900) / <alpha-value>)",
        },
        sev: {
          p0: "rgb(var(--sf-sev-p0) / <alpha-value>)",
          p1: "rgb(var(--sf-sev-p1) / <alpha-value>)",
          p2: "rgb(var(--sf-sev-p2) / <alpha-value>)",
          p3: "rgb(var(--sf-sev-p3) / <alpha-value>)",
        },
        evidence: {
          observed: "rgb(var(--sf-evidence-observed) / <alpha-value>)",
          authoritative: "rgb(var(--sf-evidence-authoritative) / <alpha-value>)",
          stated: "rgb(var(--sf-evidence-stated) / <alpha-value>)",
          declared: "rgb(var(--sf-evidence-declared) / <alpha-value>)",
        },
      },
      backgroundImage: {
        // The one fill in this design that is not a colour: what two sources
        // disagreeing about the same thing looks like.
        conflict: "var(--sf-pattern-conflict)",
      },
      borderRadius: {
        card: "var(--sf-radius-card)",
        sm: "var(--sf-radius-sm)",
        lg: "var(--sf-radius-lg)",
      },
      fontFamily: {
        sans: "var(--sf-font-sans)",
        display: "var(--sf-font-display)",
        mono: "var(--sf-font-mono)",
      },
      fontSize: {
        hero: ["var(--sf-text-hero)", { lineHeight: "1" }],
        h1: ["var(--sf-text-h1)", { lineHeight: "var(--sf-leading-display)" }],
        h2: ["var(--sf-text-h2)", { lineHeight: "var(--sf-leading-display)" }],
        h3: ["var(--sf-text-h3)", { lineHeight: "var(--sf-leading-display)" }],
        h4: ["var(--sf-text-h4)", { lineHeight: "var(--sf-leading-display)" }],
        h5: ["var(--sf-text-h5)", { lineHeight: "var(--sf-leading-display)" }],
        h6: ["var(--sf-text-h6)", { lineHeight: "1.3" }],
        body: ["var(--sf-text-body)", { lineHeight: "var(--sf-leading-body)" }],
        dense: ["var(--sf-text-dense)", { lineHeight: "1.45" }],
        small: ["var(--sf-text-small)", { lineHeight: "1.45" }],
        micro: ["var(--sf-text-micro)", { lineHeight: "1.35" }],
        tiny: ["var(--sf-text-tiny)", { lineHeight: "1.3" }],
        nano: ["var(--sf-text-nano)", { lineHeight: "1.3" }],
      },
      letterSpacing: {
        display: "var(--sf-tracking-display)",
        label: "var(--sf-tracking-label)",
        panel: "var(--sf-tracking-panel)",
      },
      spacing: {
        1: "var(--sf-space-1)",
        2: "var(--sf-space-2)",
        3: "var(--sf-space-3)",
        4: "var(--sf-space-4)",
        6: "var(--sf-space-6)",
        8: "var(--sf-space-8)",
      },
      boxShadow: {
        sm: "var(--sf-shadow-sm)",
        md: "var(--sf-shadow-md)",
        lg: "var(--sf-shadow-lg)",
      },
      keyframes: {
        // Declared in tokens.css; named here so `animate-pulse-slow` resolves.
        "sf-pulse": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.25" },
        },
      },
      animation: {
        "sf-pulse": "sf-pulse 1.4s infinite",
      },
    },
  },
  plugins: [],
};

export default config;
