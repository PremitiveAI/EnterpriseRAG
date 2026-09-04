/**
 * Server-side session handling.
 *
 * The browser NEVER receives a backend token. Access tokens live in an
 * httpOnly cookie set by our own route handlers; the refresh cookie is issued
 * by FastAPI and proxied through. No token is ever readable from client
 * JavaScript (spec §40, §42).
 */
import { cookies } from "next/headers";

export const ACCESS_COOKIE = "erag_access";
export const REFRESH_COOKIE = "erag_refresh";

export const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";
export const API = `${BACKEND_URL}/api/v1`;

export async function getAccessToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(ACCESS_COOKIE)?.value ?? null;
}

export async function isAuthenticated(): Promise<boolean> {
  return (await getAccessToken()) !== null;
}

/** Authorization header for a backend call, or throw if not signed in. */
export async function authHeaders(): Promise<Record<string, string>> {
  const token = await getAccessToken();
  if (!token) throw new Error("NOT_AUTHENTICATED");
  return { Authorization: `Bearer ${token}` };
}

export type SubjectType = "SUPER_ADMIN" | "ORG_ADMIN";

/**
 * Who is signed in, read from the access token's payload.
 *
 * The signature is NOT checked here, and deliberately so: this decides what to
 * render, never what is permitted. Every backend call carries the same token
 * and FastAPI verifies it properly, so a forged claim buys a hidden link or a
 * page that renders and then fails its first fetch with 403 - nothing more.
 * Treating this as authorization would be the bug; treating it as a hint is
 * what saves a round trip on every page load.
 */
export async function getSubjectType(): Promise<SubjectType | null> {
  const token = await getAccessToken();
  if (!token) return null;

  const payload = token.split(".")[1];
  if (!payload) return null;

  try {
    const json = Buffer.from(payload, "base64url").toString("utf8");
    const claims = JSON.parse(json) as { subject_type?: string };
    return claims.subject_type === "SUPER_ADMIN" ? "SUPER_ADMIN" : "ORG_ADMIN";
  } catch {
    return null;
  }
}
