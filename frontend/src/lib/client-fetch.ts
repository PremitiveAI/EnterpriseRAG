"use client";

import { SESSION_ENDED_PATH, isSessionEnded } from "@/lib/session-ended";

/**
 * `fetch` for authenticated browser calls, with one added behaviour: when the
 * backend says the session is over, leave for the login page instead of
 * rendering the failure.
 *
 * The access token lives in an httpOnly cookie, so the browser cannot read its
 * `exp` and cannot know it has expired. The first hint is a 401 coming back
 * from a call the user just made - which is why this lives in the response
 * path rather than in a timer.
 *
 * Drop-in: same arguments, same `Response`, so a call site changes by one word.
 */
export async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(input, init);

  // Cheap exit for the overwhelming majority. Nothing is cloned or parsed on
  // the success path.
  if (response.status !== 401) return response;

  let errorCode: unknown;
  try {
    // A clone, so the caller's `response.json()` still has a body to read.
    // Reading the original here would hand every call site an empty stream.
    errorCode = ((await response.clone().json()) as { error_code?: unknown })?.error_code;
  } catch {
    // A 401 that is not our JSON envelope - an HTML error page from a proxy,
    // an empty body. Not enough to justify throwing the user out.
    return response;
  }

  if (isSessionEnded(errorCode)) {
    // `replace`, not `assign`: the screen behind us can no longer load its
    // data, so it must not be one Back press away.
    window.location.replace(SESSION_ENDED_PATH);
  }

  return response;
}
