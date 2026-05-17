/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        'beijing-red': '#C41E3A',
        'beijing-dark': '#8B1A2E',
        'beijing-light': '#FFF0F3',
      },
    },
  },
  plugins: [],
};
