export async function sessionToken(): Promise<string> {
  const response = await fetch('/admin/csrf', { credentials: 'same-origin', cache: 'no-store' });
  if (!response.ok) throw new Error('access_denied');
  const value: unknown = await response.json();
  if (
    typeof value !== 'object' ||
    value === null ||
    !('csrf_token' in value) ||
    typeof value.csrf_token !== 'string'
  )
    throw new Error('invalid_response');
  return value.csrf_token;
}
export async function logout(): Promise<void> {
  const token = await sessionToken();
  const response = await fetch('/admin/logout', {
    method: 'POST',
    headers: { 'X-CSRF-Token': token },
    credentials: 'same-origin',
    redirect: 'manual',
  });
  if (response.type !== 'opaqueredirect' && response.status !== 303) throw new Error('logout_failed');
}
