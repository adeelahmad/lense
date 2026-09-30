/** @type {import('tailwindcss').Config} */

/* Every colour, radius and shadow comes from app/styles/tokens.css, so light and dark themes switch by CSS variables. */
const v = (name) => `var(--${name})`;
const spk = Object.fromEntries([1, 2, 3, 4, 5, 6, 7, 8].map((n) => [n, v(`spk-${n}`)]));
const emo = Object.fromEntries(
  ["neutral", "happy", "amused", "surprise", "sad", "angry", "fear", "disgust"].map((k) => [k, v(`emo-${k}`)]),
);

/* eslint-disable @typescript-eslint/no-require-imports */
module.exports = {
  darkMode: ["selector", '[data-theme="dark"]'],
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}", "./components/**/*.{js,ts,jsx,tsx,mdx}", "./lib/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["DM Sans", "ui-sans-serif", "system-ui", "sans-serif"],
        serif: ['"Source Serif 4"', "Georgia", "serif"],
        mono: ["JetBrains Mono", "ui-monospace", "Menlo", "monospace"],
      },
      colors: {
        background: v("background"),
        surface: { DEFAULT: v("surface"), neutral: v("surface-neutral") },
        border: v("border"),
        fg: {
          DEFAULT: v("text-primary"),
          strong: v("text-strong"),
          secondary: v("text-secondary"),
          muted: v("text-muted"),
          accent: v("accent-text"),
          "on-color": v("text-on-color"),
        },
        blue: { DEFAULT: v("aladdin-blue"), dark: v("blue-dark"), surface: v("intent-surface"), border: v("intent-border") },
        red: { DEFAULT: v("aladdin-red"), dark: v("red-dark"), surface: v("red-surface"), border: v("red-border") },
        green: { DEFAULT: v("aladdin-green"), dark: v("green-dark"), surface: v("green-surface"), border: v("green-border") },
        gold: { DEFAULT: v("aladdin-gold"), dark: v("gold-dark"), surface: v("gate-surface"), border: v("gate-border") },
        hl: { DEFAULT: v("hl"), word: v("hl-word") },
        spk,
        emo,
        term: { bg: v("term-bg"), fg: v("term-fg"), muted: v("term-muted") },
      },
      borderRadius: { xs: "4px", sm: "8px", md: "12px", lg: "16px", xl: "24px", pill: "999px" },
      boxShadow: { 1: v("shadow-1"), 2: v("shadow-2"), 3: v("shadow-3"), ring: v("ring-focus") },
      transitionTimingFunction: { standard: "cubic-bezier(.2,0,0,1)" },
      transitionDuration: { fast: "120ms", base: "200ms", slow: "320ms" },
      keyframes: {
        "slide-in-right": { from: { transform: "translateX(100%)" }, to: { transform: "translateX(0)" } },
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
      },
      animation: {
        "slide-in-right": "slide-in-right 200ms cubic-bezier(.2,0,0,1)",
        "fade-in": "fade-in 120ms cubic-bezier(.2,0,0,1)",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};
