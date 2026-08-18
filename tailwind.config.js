/** Compiled Tailwind build — never ship the Play CDN.
 * Design tokens: `surface` = the blue-gray charcoal darks (dark-only site),
 * `flame` = the blue-white flame accent. UI chrome stays blue/gray/white;
 * red/gold/green may appear inside artwork only.
 */
module.exports = {
  content: ["./templates/**/*.html", "./routes/**/*.py", "./app.py"],
  theme: {
    extend: {
      colors: {
        surface: {
          50: "#f2f6fb",
          100: "#e4ecf5",
          200: "#c6d4e6",
          300: "#9fb4cf",
          400: "#7690b0",
          500: "#587294",
          600: "#425877",
          700: "#2e405c",
          800: "#1d2b42",
          850: "#152134",
          900: "#0e1726",
          950: "#0a0f1a",
        },
        flame: {
          50: "#eff8ff",
          100: "#dbeffe",
          200: "#b6e2fd",
          300: "#84cffb",
          400: "#4ab5f6",
          500: "#229ae8",
          600: "#157cc6",
          700: "#1463a0",
          800: "#165384",
          900: "#17466d",
          950: "#0f2d4a",
        },
      },
      fontFamily: {
        display: ["'Playfair Display'", "Georgia", "serif"],
        body: ["Inter", "system-ui", "sans-serif"],
      },
      animation: {
        "fade-in": "fadeIn 0.5s ease-out both",
        "rise-in": "riseIn 0.5s ease-out both",
        "flame-glow": "flameGlow 5s ease-in-out infinite alternate",
      },
      keyframes: {
        fadeIn: { from: { opacity: "0" }, to: { opacity: "1" } },
        riseIn: {
          from: { opacity: "0", transform: "translateY(12px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        flameGlow: {
          from: { opacity: "0.45" },
          to: { opacity: "0.8" },
        },
      },
    },
  },
  plugins: [],
};
