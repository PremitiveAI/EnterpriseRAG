/**
 * The one BFF route that attaches NO credentials.
 *
 * Every other handler calls `proxy()`, which adds the bearer token from the
 * session cookie. Doing that here would hand a visitor's question the signed-in
 * admin's authority whenever a staff member happens to open the public page, so
 * this handler talks to the backend on its own.
 */
import { NextResponse } from "next/server";
import { API } from "@/lib/session";

// The backend rate-limits public chat per IP. Without this the whole internet
// arrives as one address - the Next.js server - and the first busy visitor
// locks out everybody else.
function forwardedFor(request: Request): string | null {
  const existing = request.headers.get("x-forwarded-for");
  if (existing) return existing;
  return request.headers.get("x-real-ip");
}

export async function POST(
  request: Request,
  { params }: { params: Promise<{ organizationId: string }> },
) {
  const { organizationId } = await params;

  let body: string;
  try {
    body = JSON.stringify(await request.json());
  } catch {
    return NextResponse.json(
      { success: false, error_code: "VALIDATION_ERROR", message: "Malformed request." },
      { status: 400 },
    );
  }

  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const ip = forwardedFor(request);
  if (ip) headers["X-Forwarded-For"] = ip;

  // Two agents can run on the synchronous path, so this is as generous as the
  // signed-in chat route.
  // Must EXCEED the backend's worst case, never sit inside it. The server skips
  // planning on a first question and caps composing at 20s with no retry, so it
  // answers within ~25s. This was 60s while the server could still spend 145s,
  // which is how a visitor got "that took too long" for an answer that was
  // genuinely still on its way.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 40_000);

  try {
    const upstream = await fetch(`${API}/public/${organizationId}/chat`, {
      method: "POST",
      headers,
      body,
      cache: "no-store",
      signal: controller.signal,
    });
    const text = await upstream.text();
    return new Response(text || "{}", {
      status: upstream.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (error) {
    const aborted = error instanceof Error && error.name === "AbortError";
    return NextResponse.json(
      {
        success: false,
        error_code: aborted ? "AI_TIMEOUT" : "SERVICE_UNAVAILABLE",
        message: aborted
          ? "That took too long to answer. Please try again."
          : "The assistant is unavailable right now.",
      },
      { status: aborted ? 504 : 503 },
    );
  } finally {
    clearTimeout(timer);
  }
}
