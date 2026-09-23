/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The backend runs separately (Docker or `make dev`). Proxying in dev keeps
  // the browser on one origin, so no CORS preflight on every request.
  async rewrites() {
    const api = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${api}/:path*` }];
  },
};

export default nextConfig;
