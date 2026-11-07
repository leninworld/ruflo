import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  resolve: {
    alias: [
      // The root ratchet must exercise the workspace MCP implementation, just
      // like the CLI suite, without requiring or trusting a stale dist build.
      {
        find: /^@claude-flow\/mcp$/,
        replacement: fileURLToPath(new URL('./v3/@claude-flow/mcp/src/index.ts', import.meta.url)),
      },
    ],
  },
});
