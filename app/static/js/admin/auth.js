// Email confirmation may redirect here with an implicit Supabase session.
// Login uses server-side sessions only; discard callback tokens from the URL.
(() => {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  if (fragment.has('access_token') || fragment.has('refresh_token') || fragment.has('error')) {
    window.history.replaceState(null, '', window.location.pathname + window.location.search);
  }
})();
