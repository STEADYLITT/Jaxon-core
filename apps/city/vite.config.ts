import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The built City is committed under services/api (like the vendored three.js) so the API
// image needs no Node build; CI rebuilds it and fails if the commit is stale.
export default defineConfig({
  base: "/city/",
  plugins: [react()],
  build: {
    outDir: "../../services/api/jackson_api/city_dist",
    emptyOutDir: true,
    sourcemap: false,
    modulePreload: { polyfill: false },
    chunkSizeWarningLimit: 1600,
    // Library code in vendor-* chunks, ours in app chunks, so "no random activity in the
    // production bundle" can be checked on our own code (libraries use their own ids).
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return undefined;
          return /three|@react-three|postprocessing|maath|camera-controls|troika|stats/.test(id)
            ? "vendor-3d" : "vendor";
        },
      },
    },
  },
});
