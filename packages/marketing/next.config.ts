import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs";

const nextConfig: NextConfig = {
  images: { unoptimized: true },
  turbopack: {},
};

export default withSentryConfig(nextConfig, {
  org: "cerid-ai",
  project: "cerid-ai-marketing",
  authToken: process.env.SENTRY_AUTH_TOKEN,
  widenClientFileUpload: true,
  tunnelRoute: "/monitoring",
  silent: !process.env.CI,
});
