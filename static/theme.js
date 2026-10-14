(() => {
  const storageKey = "meld-theme";
  const savedTheme = localStorage.getItem(storageKey);
  document.documentElement.dataset.theme = savedTheme === "dark" ? "dark" : "light";

  function updateThemeButton(button) {
    const isDark = document.documentElement.dataset.theme === "dark";
    button.textContent = isDark ? "☀" : "☾";
    button.setAttribute("aria-label", isDark ? "Hellmodus aktivieren" : "Dunkelmodus aktivieren");
    button.title = isDark ? "Hellmodus aktivieren" : "Dunkelmodus aktivieren";
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".theme-toggle").forEach((button) => {
      updateThemeButton(button);
      button.addEventListener("click", () => {
        const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
        document.documentElement.dataset.theme = theme;
        localStorage.setItem(storageKey, theme);
        updateThemeButton(button);
      });
    });
  });

  window.addEventListener("storage", (event) => {
    if (event.key === storageKey) {
      document.documentElement.dataset.theme = event.newValue === "dark" ? "dark" : "light";
      document.querySelectorAll(".theme-toggle").forEach(updateThemeButton);
    }
  });
})();
