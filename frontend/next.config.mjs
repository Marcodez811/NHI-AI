/** @type {import('next').NextConfig} */
const backendUrl = process.env.BACKEND_URL || process.env.BACKEND_INTERNAL_URL || "http://localhost:8000";

const nextConfig = {
  experimental: {
    proxyClientMaxBodySize: '300mb',
  },
  output: "standalone",
  images: {
    unoptimized: true,
  },
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${backendUrl}/api/v1/:path*` }];
  },
}

export default nextConfig
