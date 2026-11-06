export const GET = () =>
  new Response('ok', { headers: { 'Cache-Control': 'no-store', 'Content-Type': 'text/plain' } });
