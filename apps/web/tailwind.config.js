/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{html,js,svelte,ts}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Named "ink" so the palette is a design decision, not a wall of grey-800s.
        ink: {
          50: '#f6f7f9',
          100: '#e8eaef',
          200: '#cbd0db',
          300: '#a3acbd',
          400: '#74809a',
          500: '#55617b',
          600: '#414b61',
          700: '#333b4d',
          800: '#1e2431',
          850: '#171c27',
          900: '#12161f',
          950: '#0b0e14'
        },
        accent: {
          400: '#7dd3fc',
          500: '#38bdf8',
          600: '#0ea5e9'
        },
        // Citation chips use a distinct hue so "this is a source" is learnable.
        cite: {
          400: '#fbbf24',
          500: '#f59e0b'
        }
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'monospace']
      }
    }
  },
  plugins: []
};
