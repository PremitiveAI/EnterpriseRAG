/**
 * Streams the multipart body through to FastAPI with the bearer token attached
 * server-side. The file itself is never buffered into a JS string here.
 */
import { NextResponse } from "next/server";
import { API, authHeaders } from "@/lib/session";

export async function POST(request: Request) {
  let headers: Record<string, string>;
  try {
    headers = await authHeaders();
  } catch {
    return NextResponse.json(
      { success: false, error_code: "UNAUTHORIZED", message: "Not signed in." },
      { status: 401 },
    );
  }

  try {
    const form = await request.formData();
    const upstream = await fetch(`${API}/admin/documents/upload`, {
      method: "POST",
      headers,
      body: form,
      cache: "no-store",
    });
    return NextResponse.json(await upstream.json(), { status: upstream.status });
  } catch {
    return NextResponse.json(
      {
        success: false,
        error_code: "SERVICE_UNAVAILABLE",
        message: "Could not reach the server.",
      },
      { status: 503 },
    );
  }
}
