import { NextResponse } from "next/server";
import { API, authHeaders } from "@/lib/session";

export async function GET() {
  try {
    const upstream = await fetch(`${API}/config/upload-limits`, {
      headers: await authHeaders(),
      cache: "no-store",
    });
    return NextResponse.json(await upstream.json(), { status: upstream.status });
  } catch {
    return NextResponse.json(
      { success: false, error_code: "UNAUTHORIZED", message: "Not signed in." },
      { status: 401 },
    );
  }
}
