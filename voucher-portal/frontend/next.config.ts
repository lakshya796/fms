import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // "standalone" emits a self-contained server bundle with only the node_modules
  // it actually uses - which is what keeps the runtime container small and lets
  // the Dockerfile skip installing dependencies a second time.
  output: "standalone",
  // Without this, Next walks up to the parent repository, decides it is in a
  // monorepo, and nests the bundle under .next/standalone/<path>/ - so
  // `node server.js` in the container finds nothing. Pinning the root keeps
  // the output flat whatever the folder is checked out inside.
  outputFileTracingRoot: __dirname,
  reactStrictMode: true,
};

export default nextConfig;
