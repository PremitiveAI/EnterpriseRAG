/**
 * Request an OTP. Public - the caller has no token yet, by definition.
 *
 * The upstream response is forwarded verbatim, including its deliberately
 * conditional message: it is identical whether or not the identifier exists,
 * so the response cannot be used to enumerate accounts.
 */
import { NextResponse } from "next/server";
import { API } from "@/lib/session";

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

  try {
    const upstream = await fetch(`${API}/auth/otp/request`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    return NextResponse.json(await upstream.json(), { status: upstream.status });
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
}
