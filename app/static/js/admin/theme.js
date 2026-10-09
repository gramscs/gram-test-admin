(function () {
    'use strict';
    var key = 'gram-admin-theme';
    var root = document.documentElement;
    var system = window.matchMedia('(prefers-color-scheme: dark)');
    var preference = null;
    try { preference = localStorage.getItem(key); } catch (error) {}
    if (preference !== 'light' && preference !== 'dark') preference = null;

    function apply(theme) {
        root.setAttribute('data-bs-theme', theme);
        root.style.colorScheme = theme;
        document.querySelectorAll('[data-theme-toggle]').forEach(function (button) {
            var dark = theme === 'dark';
            button.setAttribute('aria-pressed', String(dark));
            button.setAttribute('aria-label', 'Switch to ' + (dark ? 'light' : 'dark') + ' mode');
            button.title = button.getAttribute('aria-label');
            button.querySelector('[data-theme-icon]').className = 'fa ' + (dark ? 'fa-sun-o' : 'fa-moon-o');
            button.querySelector('[data-theme-label]').textContent = dark ? 'Light mode' : 'Dark mode';
        });
    }
    apply(preference || (system.matches ? 'dark' : 'light'));
    document.addEventListener('DOMContentLoaded', function () {
        apply(root.getAttribute('data-bs-theme'));
        document.querySelectorAll('[data-theme-toggle]').forEach(function (button) {
            button.addEventListener('click', function () {
                preference = root.getAttribute('data-bs-theme') === 'dark' ? 'light' : 'dark';
                try { localStorage.setItem(key, preference); } catch (error) {}
                apply(preference);
            });
        });
    });
    system.addEventListener('change', function (event) {
        if (!preference) apply(event.matches ? 'dark' : 'light');
    });
    window.addEventListener('storage', function (event) {
        if (event.key !== key) return;
        preference = event.newValue === 'light' || event.newValue === 'dark' ? event.newValue : null;
        apply(preference || (system.matches ? 'dark' : 'light'));
    });
})();
