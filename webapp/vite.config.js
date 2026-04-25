import { defineConfig } from "vite";

export default defineConfig({
    root: "public",
    build: {
        outDir: "../dist",
        emptyOutDir: true,
        sourcemap: true,
    },
    server: {
        port: 5173,
        proxy: {
            "/api": {
                target: "http://localhost:8765",
                changeOrigin: true,
                ws: true,
            },
        },
    },
});
