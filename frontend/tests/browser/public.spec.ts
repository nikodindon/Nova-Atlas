import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
test.beforeEach(async ({ page, request }) => {
  await request.get('http://127.0.0.1:18055/__test/reset');
  await page.route('**/*', (route) => {
    const url = new URL(route.request().url());
    if (url.hostname === '127.0.0.1') return route.continue();
    return route.abort();
  });
  await page.addInitScript(() => {
    const media = HTMLMediaElement.prototype;
    Object.defineProperty(media, 'paused', {
      get() {
        return !(this as HTMLMediaElement & { __playing?: boolean }).__playing;
      },
    });
    media.play = function () {
      (this as HTMLMediaElement & { __playing?: boolean }).__playing = true;
      this.dispatchEvent(new Event('playing'));
      return Promise.resolve();
    };
    media.pause = function () {
      (this as HTMLMediaElement & { __playing?: boolean }).__playing = false;
    };
    media.load = function () {};
    (window as unknown as { violations: string[] }).violations = [];
    document.addEventListener('securitypolicyviolation', (event) =>
      (window as unknown as { violations: string[] }).violations.push(
        JSON.stringify({
          directive: event.effectiveDirective,
          source: event.sourceFile,
          line: event.lineNumber,
          sample: event.sample,
          blocked: event.blockedURI,
        }),
      ),
    );
  });
});
test('editorial SSR, inert content, images, keyboard and accessibility', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  const response = await page.goto('/');
  await expect(page.getByRole('heading', { name: 'L’actualité en perspective.' })).toBeVisible();
  expect(await response!.text()).not.toMatch(/<[^>]+\son(?:load|error)=/i);
  expect(response?.headers()['content-security-policy']).toContain("object-src 'none'");
  expect(response?.headers()['content-security-policy']).not.toContain('unsafe-eval');
  expect(response?.headers()['content-security-policy']).not.toContain(
    "script-src 'self' 'unsafe-inline'",
  );
  await expect(page.locator('figure img').first()).toBeVisible();
  await expect(page.getByText('CC BY 4.0')).toBeVisible();
  await page.goto('/direct');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Aller au contenu' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('main')).toBeFocused();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(
    page.getByText('<img src=x onerror="window.pwned=1"> 日本語 العربية', { exact: true }),
  ).toBeVisible();
  expect(await page.evaluate(() => 'pwned' in window)).toBe(false);
  expect(await page.locator('main script,main img[src="x"],a[href^="javascript:"]').count()).toBe(
    0,
  );
  expect(
    await page.evaluate(() => (window as unknown as { violations: string[] }).violations),
  ).toEqual([]);
  const a11y = await new AxeBuilder({ page }).analyze();
  expect(a11y.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious')).toEqual(
    [],
  );
  expect(errors).toEqual([]);
});
test('one persistent audio across public navigation, channels and bounded reconnect', async ({
  page,
}) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Écouter', exact: true }).click();
  await page.evaluate(() => {
    (window as unknown as { savedAudio: Element | null }).savedAudio =
      document.querySelector('audio');
  });
  for (const path of [
    '/direct',
    '/evenements',
    '/evenements/event-fixture',
    '/archives',
    '/archives/20261106',
    '/rapports/20261106',
    '/editions-v2/20261106/matin',
  ]) {
    await page.locator('main').evaluate((_, target) => {
      const a = document.createElement('a');
      a.href = target;
      document.querySelector('main')!.append(a);
      a.click();
    }, path);
    await page.waitForURL('**' + path);
    expect(
      await page.evaluate(
        () =>
          (window as unknown as { savedAudio: Element }).savedAudio ===
          document.querySelector('audio'),
      ),
    ).toBe(true);
  }
  await page.getByLabel('Canal', { exact: true }).selectOption('android');
  await expect(page.locator('audio')).toHaveAttribute('src', '/stream/nova-android');
  await page.locator('audio').dispatchEvent('error');
  await expect(page.getByText('Reconnexion…')).toBeVisible();
});
test('category filter, theme, English and unavailable API', async ({ page }) => {
  await page.goto('/direct');
  await page.getByLabel('Filtrer par catégorie').selectOption('tech');
  await expect(page.locator('.article-card')).toHaveCount(2);
  await page.getByRole('button', { name: 'Changer de thème' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.getByLabel('Langue de l’interface').selectOption('en');
  await expect(page.getByRole('heading', { name: 'Live feed', exact: true })).toBeVisible();
  await page.goto('/direct?category=unavailable');
  await expect(page.getByRole('alert')).toContainText('temporarily unavailable');
});
test('Flask session login, private reads, CSRF, expiry and logout', async ({ page, request }) => {
  await page.goto('/administration');
  await page.waitForURL('**/connexion');
  await page.getByLabel('Identifiant', { exact: true }).fill('admin-test');
  await page.getByLabel('Mot de passe', { exact: true }).fill('synthetic-test-password');
  await page.getByRole('button', { name: 'Connexion', exact: true }).click();
  await page.waitForURL('**/administration');
  await page.goto('/administration/sources');
  await expect(page.getByRole('heading', { name: 'Source de démonstration' })).toBeVisible();
  const missing = await page.evaluate(async () => {
    const r = await fetch('/api/admin/sources', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    return r.status;
  });
  expect(missing).toBe(403);
  await page.goto('/administration/radio');
  await expect(page.getByRole('heading', { name: 'Radio Studio', exact: true })).toBeVisible();
  await request.get('http://127.0.0.1:18055/__test/expire');
  await page.goto('/administration');
  await page.waitForURL('**/connexion');
  await page.getByLabel('Identifiant', { exact: true }).fill('admin-test');
  await page.getByLabel('Mot de passe', { exact: true }).fill('synthetic-test-password');
  await page.getByRole('button', { name: 'Connexion', exact: true }).click();
  await page.waitForURL('**/administration');
  await page.getByRole('button', { name: 'Déconnexion', exact: true }).click();
  await page.waitForURL('**/connexion');
  expect((await page.request.get('/admin/csrf')).status()).toBe(401);
});
test('revoked image becomes local fallback and reports keep hostile HTML inert', async ({
  page,
  request,
}) => {
  await request.get('http://127.0.0.1:18055/__test/revoke');
  await page.goto('/categories/science');
  expect(await page.locator('figure img').count()).toBe(0);
  await page.goto('/rapports/20261106');
  await expect(page.getByText('<script>window.pwned=4</script>', { exact: true })).toBeVisible();
  expect(await page.locator('main script').count()).toBe(0);
  expect(await page.evaluate(() => 'pwned' in window)).toBe(false);
});
