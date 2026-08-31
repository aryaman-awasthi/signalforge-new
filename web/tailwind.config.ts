import type { Config } from "tailwindcss";

// Colors resolve to design-system tokens (CSS custom properties), so the
// palette is themed in exactly one place: design-system/tokens.css.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./features/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: "rgb(var(--sf-surface) / <alpha-value>)",
        "surface-raised": "rgb(var(--sf-surface-raised) / <alpha-value>)",
        ink: "rgb(var(--sf-ink) / <alpha-value>)",
        "ink-muted": "rgb(var(--sf-ink-muted) / <alpha-value>)",
        accent: "rgb(var(--sf-accent) / <alpha-value>)",
        "accent-strong": "rgb(var(--sf-accent-strong) / <alpha-value>)",
        ok: "rgb(var(--sf-ok) / <alpha-value>)",
        warn: "rgb(var(--sf-warn) / <alpha-value>)",
        danger: "rgb(var(--sf-danger) / <alpha-value>)",
        line: "rgb(var(--sf-line) / <alpha-value>)",
      },
      borderRadius: {
        card: "var(--sf-radius-card)",
      },
      fontFamily: {
        sans: "var(--sf-font-sans)",
        mono: "var(--sf-font-mono)",
      },
    },
  },
  plugins: [],
};

export default config;
