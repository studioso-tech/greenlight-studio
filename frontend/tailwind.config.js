/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        so: {
          sky: "#E0F2F7",      // S.O Azure Mist
          sage: "#E8F0E8",     // S.O Sage Glass
          charcoal: "#2F3E46", // Slate Charcoal
          navy: "#1B4965",     // Deep Sea Blue
          card: "rgba(255, 255, 255, 0.85)",
          gold: "#E0A96D",
        },
      },
    },
  },
  plugins: [],
}
