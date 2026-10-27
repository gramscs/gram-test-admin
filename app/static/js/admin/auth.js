// Old confirmation links may still carry implicit tokens. They never authenticate
// this app; discard them. Current registration uses the server's PKCE callback.
(() => {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  if (fragment.has('access_token') || fragment.has('refresh_token') || fragment.has('error')) {
    window.history.replaceState(null, '', window.location.pathname + window.location.search);
  }
})();
