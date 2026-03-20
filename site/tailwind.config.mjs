/** @type {import('tailwindcss').Config} */
export default {
  content: ["./src/**/*.{astro,html,js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: "#0a0a0f",
          card: "#12121a",
          elevated: "#1a1a2e",
        },
        border: "#2a2a3e",
        accent: {
          DEFAULT: "#6366f1",
          hover: "#8b5cf6",
        },
        gfi: "#22c55e",
        bug: "#f59e0b",
        hard: "#ef4444",
        points: "#10b981",
      },
      fontFamily: {
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "SF Mono",
          "Menlo",
          "Consolas",
          "Liberation Mono",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
};
