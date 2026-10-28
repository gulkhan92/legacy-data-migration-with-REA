import type { Config } from "tailwindcss";

/** Every value comes from src/styles/tokens.css: change a token there and the whole site follows. */
const channel = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    container: { center: true, padding: { DEFAULT: "1rem", md: "2rem" }, screens: { "2xl": "1280px" } },
    extend: {
      colors: {
        navy: { DEFAULT: channel("navy"), 800: channel("navy-800"), 700: channel("navy-700") },
        primary: { DEFAULT: channel("primary"), hover: channel("primary-hover"), soft: channel("primary-soft"), ink: channel("primary-ink"), foreground: channel("primary-foreground") },
        teal: { DEFAULT: channel("teal"), text: channel("teal-text"), soft: channel("teal-soft") },
        violet: { DEFAULT: channel("violet"), soft: channel("violet-soft") },
        background: channel("background"),
        surface: { DEFAULT: channel("surface"), muted: channel("surface-muted"), raised: channel("surface-raised") },
        foreground: { DEFAULT: channel("foreground"), muted: channel("foreground-muted"), subtle: channel("foreground-subtle") },
        border: { DEFAULT: channel("border"), strong: channel("border-strong") },
        ring: channel("ring"),
        success: { DEFAULT: channel("success"), soft: channel("success-soft") },
        warning: { DEFAULT: channel("warning"), soft: channel("warning-soft") },
        danger: { DEFAULT: channel("danger"), soft: channel("danger-soft") },
      },
      fontFamily: {
        sans: ["Inter Variable", "Inter Fallback", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      fontSize: {
        xs: ["var(--text-xs)", { lineHeight: "var(--leading-xs)" }],
        sm: ["var(--text-sm)", { lineHeight: "var(--leading-sm)" }],
        base: ["var(--text-base)", { lineHeight: "var(--leading-base)" }],
        lg: ["var(--text-lg)", { lineHeight: "var(--leading-lg)" }],
        xl: ["var(--text-xl)", { lineHeight: "var(--leading-xl)" }],
        "2xl": ["var(--text-2xl)", { lineHeight: "var(--leading-2xl)" }],
        "3xl": ["var(--text-3xl)", { lineHeight: "var(--leading-3xl)", letterSpacing: "-0.01em" }],
        "4xl": ["var(--text-4xl)", { lineHeight: "var(--leading-4xl)", letterSpacing: "-0.02em" }],
        "5xl": ["var(--text-5xl)", { lineHeight: "var(--leading-5xl)", letterSpacing: "-0.025em" }],
      },
      borderRadius: { sm: "var(--radius-sm)", md: "var(--radius-md)", lg: "var(--radius-lg)", xl: "var(--radius-xl)" },
      boxShadow: { sm: "var(--shadow-sm)", md: "var(--shadow-md)", lg: "var(--shadow-lg)", focus: "var(--shadow-focus)" },
      maxWidth: { site: "var(--max-width)", prose: "68ch" },
      keyframes: {
        "accordion-down": { from: { height: "0" }, to: { height: "var(--radix-accordion-content-height)" } },
        "accordion-up": { from: { height: "var(--radix-accordion-content-height)" }, to: { height: "0" } },
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "slide-in-left": { from: { transform: "translateX(-100%)" }, to: { transform: "translateX(0)" } },
        "slide-out-left": { from: { transform: "translateX(0)" }, to: { transform: "translateX(-100%)" } },
        "menu-in": { from: { opacity: "0", transform: "translateY(-4px)" }, to: { opacity: "1", transform: "translateY(0)" } },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
        "fade-in": "fade-in 0.2s ease-out",
        "slide-in-left": "slide-in-left 0.25s ease-out",
        "slide-out-left": "slide-out-left 0.2s ease-in",
        "menu-in": "menu-in 0.15s ease-out",
      },
    },
  },
  plugins: [],
} satisfies Config;
