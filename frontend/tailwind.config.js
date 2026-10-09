/** Tailwind + DaisyUI build for static/app.css. Run `npm ci && npm run build` in frontend/. */
module.exports = {
  // Every file that can contain a class name: templates and Python. static/app.js isn't scanned
  // (its JS yields false matches like !toggle); a class it adds must also appear in a template.
  content: ["../app/templates/**/*.html", "../app/**/*.py"],
  plugins: [require("daisyui")],
  daisyui: {
    // base.html sets data-theme to light or dark before first paint.
    themes: ["light", "dark"],
    logs: false,
  },
};
