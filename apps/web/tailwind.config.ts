import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: { ink: "#07080a", panel: "#101216", line: "#292d35", mist: "#aeb5c2", acid: "#d7ff5f" },
      boxShadow: { glow: "0 0 0 1px rgba(215,255,95,.14), 0 24px 80px rgba(0,0,0,.36)" },
      fontFamily: { display: ["var(--font-display)", "ui-sans-serif", "system-ui"], sans: ["var(--font-sans)", "ui-sans-serif", "system-ui"] },
      backgroundImage: { grid: "linear-gradient(rgba(255,255,255,.045) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.045) 1px, transparent 1px)" }
    }
  },
  plugins: []
};

export default config;
