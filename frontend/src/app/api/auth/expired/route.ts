import { NextResponse } from "next/server";
import { ACCESS_COOKIE, REFRESH_COOKIE, getSubjectType } from "@/lib/session";

/**
 * The exit for a session that has ended.
 *
 * Everything that detects an expired token sends the browser here rather than
 * to a login page directly, because two things have to happen and only one of
 * them is a redirect:
 *
 *  1. The dead cookies are cleared. `isAuthenticated()` is satisfied by the
 *     presence of `erag_access`, never by its contents - leaving an expired
 *     one in place would let the dashboard layout wave the user straight back
 *     into screens that cannot load.
 *  2. They land at the door that is actually theirs.
 */
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  // Read the subject BEFORE the cookie goes. There are two sign-in pages -
  // /login is the Super Admin's password form, /organization/login is the
  // Org Admin's OTP form - and an Org Admin dropped at /login would be staring
  // at a password field they have no password for.
  //
  // The claim is unverified, and that is fine: it chooses which form to show,
  // never what anyone is allowed to do. The worst a forged one buys is the
  // wrong login page.
  const subject = await getSubjectType();
  const destination = subject === "ORG_ADMIN" ? "/organization/login" : "/login";

  const response = NextResponse.redirect(new URL(destination, request.url));
  response.cookies.delete(ACCESS_COOKIE);
  response.cookies.delete(REFRESH_COOKIE);
  return response;
}
