import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    pool: 'threads',
    maxWorkers: 1,
    fileParallelism: false,
    testTimeout: 10_000,
  },
});
