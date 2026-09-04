import type { NextConfig } from "next";

/**
 * Framing rules.
 *
 * The public chatbot exists to be embedded in someone else's website, so it
 * must be framable by anyone. Every authenticated screen must not be, or a
 * hostile page could frame the dashboard and clickjack a signed-in admin into
 * deleting a document.
 *
 * These are listed one route at a time rather than as a blanket rule plus an
 * exception, because Next.js applies every matching entry instead of letting a
 * later one override an earlier one - a "deny all, then allow the widget" pair
 * would emit two contradictory headers and the browser would take the strictest.
 */
const NO_FRAMING = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
];

const ADMIN_ROUTES = [
  "/",
  "/login",
  "/organization/login",
  "/dashboard",
  "/documents",
  "/upload",
  "/chat/:path*",
  "/chat",
  "/super-admin/:path*",
];

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The backend URL is server-side only. It is deliberately NOT exposed as a
  // NEXT_PUBLIC_ value: those are inlined into the client bundle at build time.
  env: {},

  async headers() {
    return [
      ...ADMIN_ROUTES.map((source) => ({ source, headers: NO_FRAMING })),
      {
        // The widget's iframe target. Framable on purpose, and safe to be:
        // it holds no session cookie and can reach only published documents.
        source: "/:organizationId/chat",
        headers: [{ key: "Content-Security-Policy", value: "frame-ancestors *" }],
      },
      {
        // The loader third-party pages include with a <script> tag.
        source: "/widget.js",
        headers: [
          { key: "Access-Control-Allow-Origin", value: "*" },
          { key: "Cache-Control", value: "public, max-age=300" },
        ],
      },
    ];
  },
};

export default nextConfig;
