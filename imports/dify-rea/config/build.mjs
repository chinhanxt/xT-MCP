import { build } from 'esbuild';
import { mkdir, copyFile } from 'node:fs/promises';
await mkdir('dist', { recursive: true });
await build({
  entryPoints: ['src/web/main.tsx'],
  bundle: true,
  minify: true,
  sourcemap: true,
  outdir: 'dist',
  entryNames: 'app',
  loader: { '.woff': 'file', '.woff2': 'file' },
});
await copyFile('src/web/index.html', 'dist/index.html');
