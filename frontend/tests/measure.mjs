// Explicit synthetic-browser measurements; no production service or remote URL.
import { chromium } from '@playwright/test';
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  headless: true,
  args: ['--disable-background-networking', '--disable-dev-shm-usage'],
});
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.route('**/*', (r) =>
    new URL(r.request().url()).hostname === '127.0.0.1' ? r.continue() : r.abort(),
  );
  const start = performance.now();
  await page.goto('http://127.0.0.1:4173/');
  await page.getByRole('button', { name: 'Écouter', exact: true }).waitFor();
  const metrics = await page.evaluate(() => {
    const n = performance.getEntriesByType('navigation')[0];
    const resources = performance.getEntriesByType('resource');
    return {
      document_bytes: n?.encodedBodySize,
      dom_content_loaded_ms: n?.domContentLoadedEventEnd,
      resource_requests: resources.length,
      javascript_transfer_bytes: resources
        .filter((r) => r.name.endsWith('.js'))
        .reduce((sum, r) => sum + r.transferSize, 0),
    };
  });
  const report = {
    environment:
      'Local Chromium, 1440x1000, synthetic mock Flask through loopback gateway; cold browser; single run, not a benchmark distribution',
    elapsed_to_player_ms: Math.round(performance.now() - start),
    ...metrics,
    lighthouse: 'not measured',
    container_memory_cpu: 'not measured',
  };
  console.log(JSON.stringify(report, null, 2));
  await page.screenshot({ path: '/tmp/s12-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: '/tmp/s12-mobile.png', fullPage: true });
} finally {
  await browser.close();
}
