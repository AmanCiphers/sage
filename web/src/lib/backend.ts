/**
 * Backend address helpers.
 *
 * REST and SSE go through the Next.js rewrite on the same origin, so no base
 * is needed. WebSockets are not covered by Next.js rewrites, so the browser
 * must reach the API directly. Resolved lazily because `window` is absent
 * during SSR; override with NEXT_PUBLIC_SAGE_WS when the API lives elsewhere
 * or behind TLS.
 */
export function wsBase(): string {
  if (process.env.NEXT_PUBLIC_SAGE_WS) return process.env.NEXT_PUBLIC_SAGE_WS;
  const secure = typeof window !== "undefined" && window.location.protocol === "https:";
  const host = typeof window !== "undefined" ? window.location.hostname : "localhost";
  return `${secure ? "wss" : "ws"}://${host}:8000`;
}
