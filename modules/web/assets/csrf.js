if (!window.atlasSecureFetch) {
  window.atlasSecureFetch = true;
  const originalFetch = window.fetch.bind(window);
  let pendingToken = null;
  window.fetch = function(input, options) {
    const req = new Request(input, options);
    if (new URL(req.url).origin !== location.origin || ['GET','HEAD','OPTIONS'].includes(req.method)) return originalFetch(req);
    const token = document.querySelector('meta[name="csrf-token"]');
    if (token) { req.headers.set('X-CSRF-Token', token.content); return originalFetch(req); }
    // Public HTML never embeds administrator tokens. Acquire only for an explicit action.
    if (!pendingToken) pendingToken = originalFetch('/admin/csrf').then(r=>r.ok?r.json():{}).then(data=>data.csrf_token||null).catch(()=>null);
    return pendingToken.then(value=>{if(value)req.headers.set('X-CSRF-Token',value);return originalFetch(req);});
  };
  document.addEventListener('submit', function(e) {
    const form = e.target;
    const token = document.querySelector('meta[name="csrf-token"]');
    if (token && form.method.toUpperCase() !== 'GET' && new URL(form.action).origin === location.origin) {
      let field = form.querySelector('[name="csrf_token"]');
      if (!field) { field = document.createElement('input'); field.type='hidden'; field.name='csrf_token'; form.appendChild(field); }
      field.value = token.content;
    }
  }, true);
}
