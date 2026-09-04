/**
 * BFF login. The browser posts here; this handler talks to FastAPI and stores
 * the tokens in httpOnly cookies. No token is ever returned to the client.
 */
import { NextResponse } from "next/server";
import { ACCESS_COOKIE, API, REFRESH_COOKIE } from "@/lib/session";

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { success: false, error_code: "VALIDATION_ERROR", message: "Malformed request." },
      { status: 400 },
    );
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${API}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json(
      {
        success: false,
        error_code: "SERVICE_UNAVAILABLE",
        message: "Could not reach the server. Is the backend running?",
      },
      { status: 503 },
    );
  }

  const payload = await upstream.json().catch(() => null);

  if (!upstream.ok || !payload?.success) {
    return NextResponse.json(
      payload ?? {
        success: false,
        error_code: "INTERNAL_ERROR",
        message: "Sign in failed.",
      },
      { status: upstream.status },
    );
  }

  // Return the user, never the token.
  const response = NextResponse.json({ success: true, data: { user: payload.data.user } });

  response.cookies.set(ACCESS_COOKIE, payload.data.access_token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    path: "/",
    maxAge: payload.data.expires_in,
  });

  // FastAPI issues the refresh cookie; forward it so the session can be renewed.
  const setCookie = upstream.headers.get("set-cookie");
  const refresh = setCookie?.match(new RegExp(`${REFRESH_COOKIE}=([^;]+)`))?.[1];
  if (refresh) {
    response.cookies.set(REFRESH_COOKIE, refresh, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      sameSite: "strict",
      path: "/",
    });
  }

  return response;
}
