import type { NextConfig } from "next";

// All browser requests go to the same origin (/api/...) and are proxied to the FastAPI backend.
// Rewrites are resolved when the server starts: in `next dev` / `next start` / the standalone
// server, BACKEND_URL is read from the environment at build time and baked into the routes
// manifest. Rebuild the image (or pass --build-arg BACKEND_URL=...) to point at another backend.
// 127.0.0.1 rather than "localhost": Node may resolve localhost to IPv6 ::1 while the API listens on IPv4.
const backendUrl = (process.env.BACKEND_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");

// Proxied request bodies are buffered up to this size (default 10 MB, smaller than the backend's
// upload limit). Keep it a little above the backend's MAX_UPLOAD_MB to allow for multipart overhead.
const maxUploadMb = Number(process.env.MAX_UPLOAD_MB ?? 25);

// Dev server only: other devices (e.g. a phone on the same Wi-Fi) may load the app from these hosts.
// Comma-separated, e.g. ALLOWED_DEV_ORIGINS=192.168.1.36
const allowedDevOrigins = (process.env.ALLOWED_DEV_ORIGINS ?? "")
  .split(",")
  .map((h) => h.trim())
  .filter(Boolean);

const nextConfig: NextConfig = {
  output: "standalone",
  allowedDevOrigins,
  experimental: {
    proxyClientMaxBodySize: `${maxUploadMb + 5}mb`,
  },
  cacheComponents: true,
  partialPrefetching: true,
  poweredByHeader: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
