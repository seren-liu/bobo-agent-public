/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.{js,jsx,ts,tsx}',
    './components/**/*.{js,jsx,ts,tsx}',
    './hooks/**/*.{js,jsx,ts,tsx}',
  ],
  presets: [require('nativewind/preset')],
  theme: {
    extend: {
      colors: {
        // Bobo brand colors
        primary: '#ADFF2F',
        'primary-foreground': '#1C1C1E',
        background: '#FAFAFA',
        foreground: '#1C1C1E',
        muted: '#F5F5F5',
        'muted-foreground': '#8E8E93',
        border: '#E5E5EA',
        // Pastel card palette
        'pastel-yellow': '#FFF8D6',
        'pastel-green': '#E8F5F0',
        'pastel-blue': '#E8F4FF',
        'pastel-pink': '#FFE8EC',
        // Hero gradient
        'hero-from': '#FFB7C5',
        'hero-to': '#FFA5B4',
      },
      borderRadius: {
        '2xl': '16px',
        '3xl': '24px',
        '4xl': '32px',
      },
      fontFamily: {
        sans: ['System'],
      },
    },
  },
  plugins: [],
};
