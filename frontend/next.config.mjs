/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // A self-contained server (`.next/standalone/server.js`) with only the
  // node_modules it uses, which is what the production image runs.
  output: "standalone",
  // The demo runs on `make dev`; a framework badge in the corner of a screen
  // share or a README screenshot is not part of the product.
  devIndicators: { appIsrStatus: false, buildActivity: false },
  experimental: {
    // The rewrite proxy gives up after 30s by default, and a `full` answer that
    // regenerates takes longer than that on the free tier: the backend
    // finished the answer while the browser was shown a 500. One LLM call may
    // take up to 180s (app/core/llm.py); the proxy should not be what times out.
    proxyTimeout: 300_000,
  },
  // The backend runs separately (Docker or `make dev`). Proxying keeps the
  // browser on one origin, so no CORS preflight on every request, and in
  // production the API needs no public port of its own. The target is read by
  // `next build` and compiled into the output: set NEXT_PUBLIC_API_URL before
  // building, not only before starting.
  async rewrites() {
    const api = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${api}/:path*` }];
  },
};

export default nextConfig;
