/** Tailwind + DaisyUI build for static/app.css. Run `npm ci && npm run build` in frontend/. */
module.exports = {
  // Every file that can contain a class name: templates (including their inline scripts) and Python.
  content: ["../app/templates/**/*.html", "../app/**/*.py"],
  plugins: [require("daisyui")],
  daisyui: {
    // base.html sets data-theme to light or dark before first paint.
    themes: ["light", "dark"],
    logs: false,
  },
};
