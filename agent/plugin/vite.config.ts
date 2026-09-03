import { defineConfig } from 'vite';
import { resolve } from 'path';

export default defineConfig({
  build: {
    lib: {
      entry: resolve(__dirname, 'src/index.ts'),
      formats: ['es'],
      fileName: () => 'index.mjs',
    },
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      external: ['fs', 'path', 'url', 'os'],
    },
    target: 'node18',
    minify: false,
  },
});
