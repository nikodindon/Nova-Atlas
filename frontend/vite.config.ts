import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import { sveltekit } from '@sveltejs/kit/vite';
import adapter from '@sveltejs/adapter-node';

export default defineConfig({
  envDir: false,
  resolve: { alias: { '@lib': fileURLToPath(new URL('./src/lib', import.meta.url)) } },
  plugins: [
    sveltekit({
      adapter: adapter(),
      csp: {
        mode: 'nonce',
        directives: {
          'default-src': ['self'],
          'script-src': ['self'],
          'style-src': ['self'],
          // Exact style of the pinned Kit navigation accessibility announcer, not arbitrary inline styles.
          'style-src-attr': [
            'unsafe-hashes',
            'sha256-S8qMpvofolR8Mpjy4kQvEm7m1q8clzU4dfDH0AmvZjo=',
          ],
          'img-src': ['self'],
          'font-src': ['self'],
          'media-src': ['self'],
          'connect-src': ['self'],
          'object-src': ['none'],
          'base-uri': ['self'],
          'frame-ancestors': ['none'],
          'form-action': ['self'],
        },
      },
    }),
  ],
  build: { assetsInlineLimit: 0 },
  server: { host: '127.0.0.1' },
});
