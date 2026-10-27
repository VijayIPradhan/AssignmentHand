import type { NextConfig } from "next";

// All browser requests go to the same origin (/api/...) and are proxied to the FastAPI backend.
// Rewrites are resolved when the server starts: in `next dev` / `next start` / the standalone
// server, BACKEND_URL is read from the environment at build time and baked into the routes
// manifest. Rebuild the image (or pass --build-arg BACKEND_URL=...) to point at another backend.
const backendUrl = (process.env.BACKEND_URL ?? "http://localhost:8000").replace(/\/+$/, "");

// Proxied request bodies are buffered up to this size (default 10 MB, smaller than the backend's
// upload limit). Keep it a little above the backend's MAX_UPLOAD_MB to allow for multipart overhead.
const maxUploadMb = Number(process.env.MAX_UPLOAD_MB ?? 25);

const nextConfig: NextConfig = {
  output: "standalone",
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
