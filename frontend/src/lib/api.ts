/**
 * Server-side backend calls.
 *
 * Only ever imported from server components and route handlers — it reads the
 * httpOnly cookie, so importing it from a client component would be a build
 * error rather than a silent leak (spec §40, §42).
 */
import { API, authHeaders } from "@/lib/session";
import { isSessionEnded } from "@/lib/session-ended";
import type { ApiResponse } from "@/types/api";

export class NotAuthenticated extends Error {}

/**
 * GET a backend path, returning the parsed envelope.
 *
 * Throws `NotAuthenticated` for a session that is over — whether the cookie was
 * missing to begin with, or the token it holds came back rejected. Callers
 * already catch that and redirect, so an expired token needs no per-page
 * handling: it simply stops being a page-level error to render.
 */
export async function backendGet<T>(path: string): Promise<ApiResponse<T>> {
  let headers: Record<string, string>;
  try {
    headers = await authHeaders();
  } catch {
    throw new NotAuthenticated();
  }

  let body: ApiResponse<T>;
  try {
    const response = await fetch(`${API}${path}`, {
      headers,
      // The list changes constantly; a cached page showing a document that has
      // since been deleted is worse than a slightly slower one.
      cache: "no-store",
    });
    body = (await response.json()) as ApiResponse<T>;
  } catch {
    return {
      success: false,
      error_code: "SERVICE_UNAVAILABLE",
      message: "Could not reach the server.",
    };
  }

  // Outside the catch above, and that placement is the whole point: thrown
  // inside it, this would be swallowed and reported as an unreachable server.
  //
  // A cookie that exists proves nothing about the token inside it, so this is
  // the only place an expiry can actually be noticed on a server-rendered
  // page. Without it the page rendered "Access token has expired." and left
  // the user stranded on a screen whose only way out was Sign out.
  if (!body.success && isSessionEnded(body.error_code)) {
    throw new NotAuthenticated();
  }

  return body;
}

/** Generous by default; a chat message overrides it — two agents run on the
 * synchronous path and the whole thing can take a minute. */
const DEFAULT_TIMEOUT_MS = 30_000;

export async function proxy(
  path: string,
  init: { method: string; body?: string },
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
): Promise<Response> {
  let headers: Record<string, string>;
  try {
    headers = await authHeaders();
  } catch {
    return Response.json(
      { success: false, error_code: "UNAUTHORIZED", message: "Not signed in." },
      { status: 401 },
    );
  }

  // A chat message runs two agents on the synchronous path, so it can take a
  // minute. Node's fetch has no default timeout; without an explicit one a
  // stalled request hangs until the platform kills it and the user sees
  // nothing at all.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const upstream = await fetch(`${API}${path}`, {
      method: init.method,
      headers: init.body ? { ...headers, "Content-Type": "application/json" } : headers,
      body: init.body,
      cache: "no-store",
      signal: controller.signal,
    });

    const text = await upstream.text();
    return new Response(text || "{}", {
      status: upstream.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (error) {
    // "Could not reach the server" was shown for every failure, which made a
    // 90-second timeout and a backend that is not running look identical. They
    // need completely different fixes, so they get different messages.
    const aborted = error instanceof Error && error.name === "AbortError";
    const cause = (error as { cause?: { code?: string } })?.cause?.code ?? "";
    const refused = cause === "ECONNREFUSED" || cause === "ENOTFOUND";

    console.error("[bff] upstream call failed", {
      path,
      method: init.method,
      name: error instanceof Error ? error.name : typeof error,
      cause,
    });

    if (aborted) {
      return Response.json(
        {
          success: false,
          error_code: "AI_TIMEOUT",
          message: `The server took longer than ${Math.round(
            timeoutMs / 1000,
          )}s to answer. It may still be processing — check the conversation in a moment.`,
        },
        { status: 504 },
      );
    }

    return Response.json(
      {
        success: false,
        error_code: "SERVICE_UNAVAILABLE",
        message: refused
          ? `The backend is not reachable at ${API}. Is the API running on that port?`
          : "Could not reach the server.",
      },
      { status: 503 },
    );
  } finally {
    clearTimeout(timer);
  }
}
