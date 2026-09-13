import type { NextConfig } from "next";

const config: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${process.env.CONTROL_API_URL || "http://127.0.0.1:8088"}/api/v1/:path*`,
      },
    ];
  },
};
export default config;
