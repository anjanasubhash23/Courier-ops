import type { NextConfig } from "next";

// The browser talks only to Next.js; /api/* is forwarded to FastAPI, so there's no CORS to configure.
const apiUrl = process.env.API_URL ?? "http://localhost:8000";

const config: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/:path*` }];
  },
};

export default config;
