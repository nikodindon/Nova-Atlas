// Reproducible build budget, not a Lighthouse result or hardware benchmark.
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
const directory = '.svelte-kit/output/client';
function walk(root) {
  return fs
    .readdirSync(root, { withFileTypes: true })
    .flatMap((row) =>
      row.isDirectory() ? walk(path.join(root, row.name)) : [path.join(root, row.name)],
    );
}
const files = walk(directory);
const metrics = {
  scope: 'all client routes, gzip level 9 per file',
  javascript_gzip_bytes: 0,
  css_gzip_bytes: 0,
  javascript_files: 0,
  css_files: 0,
};
for (const file of files) {
  const type = file.endsWith('.js') ? 'javascript' : file.endsWith('.css') ? 'css' : null;
  if (type) {
    metrics[type + '_gzip_bytes'] += zlib.gzipSync(fs.readFileSync(file), { level: 9 }).length;
    metrics[type + '_files']++;
  }
}
console.log(JSON.stringify(metrics, null, 2));
if (metrics.javascript_gzip_bytes > 100 * 1024 || metrics.css_gzip_bytes > 15 * 1024)
  throw Error('Client build exceeds documented budget');
