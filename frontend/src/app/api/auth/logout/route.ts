import { NextResponse } from "next/server";
import { ACCESS_COOKIE, API, REFRESH_COOKIE, getAccessToken } from "@/lib/session";

export async function POST() {
  const token = await getAccessToken();

  // Best effort: revoke server-side, but always clear the cookies locally so a
  // backend outage cannot leave the user stuck in a session they can't end.
  if (token) {
    try {
      await fetch(`${API}/auth/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      });
    } catch {
      /* ignore */
    }
  }

  const response = NextResponse.json({ success: true });
  response.cookies.delete(ACCESS_COOKIE);
  response.cookies.delete(REFRESH_COOKIE);
  return response;
}
