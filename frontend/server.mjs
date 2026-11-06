// Runtime configuration is fixed by the operator, never read from a request.
try {
  const url = new URL(process.env.FLASK_ORIGIN || '');
  if (
    !['http:', 'https:'].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.pathname !== '/' ||
    url.search ||
    url.hash ||
    !['http', 'https'].includes(process.env.ATLAS_PUBLIC_SCHEME || '')
  )
    throw Error();
  await import('./build/index.js');
} catch {
  console.error('Frontend configuration invalid or startup failed');
  process.exitCode = 78;
}
