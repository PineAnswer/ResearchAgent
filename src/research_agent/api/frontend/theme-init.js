// Applies the saved theme before first paint to avoid a light-mode flash.
// Loaded synchronously in <head>; keep this file tiny and dependency-free.
(function () {
  "use strict";
  var stored = null;
  try {
    stored = window.localStorage.getItem("research-agent.theme");
  } catch (_err) {
    // Storage unavailable: fall back to the system preference below.
  }
  var theme;
  if (stored === "dark" || stored === "light") {
    theme = stored;
  } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
    theme = "dark";
  } else {
    theme = "light";
  }
  document.documentElement.dataset.theme = theme;
})();
