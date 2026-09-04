/**
 * When a session is over, and where it goes.
 *
 * Deliberately import-free: the browser, server components and route handlers
 * all need these three things, and a module that reaches for `next/headers` or
 * `window` could not be shared by all three.
 */

/**
 * Not `/login`. A dead session leaves a stale `erag_access` cookie behind, and
 * `isAuthenticated()` in `lib/session.ts` is satisfied by the cookie's mere
 * presence - so going straight to a login page would leave the user one click
 * away from walking back into a dashboard that cannot load. The route handler
 * clears the cookies first, then forwards to the right door.
 */
export const SESSION_ENDED_PATH = "/api/auth/expired";

/**
 * The two 401 codes that mean "this credential is finished": the access token
 * outlived its `exp` (TOKEN_EXPIRED), or it was missing, malformed, of the
 * wrong type, or belongs to an account that is gone or deactivated
 * (UNAUTHORIZED). Both are unrecoverable without signing in again.
 *
 * FORBIDDEN is absent on purpose. A Super Admin calling an organization-scoped
 * endpoint gets 403, and their session is perfectly valid - signing them out
 * for opening the wrong page would be a bug, not a safeguard.
 */
const SESSION_ENDED_CODES = new Set(["TOKEN_EXPIRED", "UNAUTHORIZED"]);

/** Whether an error envelope's `error_code` means the session has ended. */
export function isSessionEnded(errorCode: unknown): boolean {
  return typeof errorCode === "string" && SESSION_ENDED_CODES.has(errorCode);
}
