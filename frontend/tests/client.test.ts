import { afterEach, expect, test, vi } from 'vitest';
import { logout } from '../src/lib/client';
afterEach(()=>vi.unstubAllGlobals());
test('logout requires Flask CSRF and confirmed redirect',async()=>{
 const fetcher=vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({csrf_token:'synthetic'}),{status:200})).mockResolvedValueOnce(new Response(null,{status:303}));vi.stubGlobal('fetch',fetcher);
 await expect(logout()).resolves.toBeUndefined();expect(fetcher.mock.calls[1][0]).toBe('/admin/logout');expect(fetcher.mock.calls[1][1].headers['X-CSRF-Token']).toBe('synthetic');
});
test('failed logout is never reported as success',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({csrf_token:'synthetic'}),{status:200})).mockResolvedValueOnce(new Response(null,{status:403})));
 await expect(logout()).rejects.toThrow('logout_failed');
});
