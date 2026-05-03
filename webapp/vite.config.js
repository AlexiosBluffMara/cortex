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
        host: "0.0.0.0",
        allowedHosts: true,
        proxy: {
            "/api": {
                target: "http://localhost:8773",
                changeOrigin: true,
                ws: true,
            },
        },
    },
});
