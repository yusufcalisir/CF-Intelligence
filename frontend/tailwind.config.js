/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          bg: '#f8fafc',
          card: '#ffffff',
          accent: '#4f46e5',
          purple: '#7c3aed',
          danger: '#dc2626',
          warning: '#d97706',
          success: '#059669',
        }
      },
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
      }
    },
  },
  plugins: [],
}
