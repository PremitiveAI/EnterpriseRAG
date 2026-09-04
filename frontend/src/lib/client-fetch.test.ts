import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch } from "@/lib/client-fetch";
import { SESSION_ENDED_PATH, isSessionEnded } from "@/lib/session-ended";

const replace = vi.fn();

function respond(status: number, body: unknown): Response {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  replace.mockClear();
  // jsdom refuses to navigate, so the one side effect under test is stubbed.
  vi.stubGlobal("location", { ...window.location, replace });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("isSessionEnded", () => {
  it("accepts the two codes that mean the credential is finished", () => {
    expect(isSessionEnded("TOKEN_EXPIRED")).toBe(true);
    expect(isSessionEnded("UNAUTHORIZED")).toBe(true);
  });

  it("rejects a 403: wrong page, not a dead session", () => {
    expect(isSessionEnded("FORBIDDEN")).toBe(false);
  });

  it("rejects anything that is not a string", () => {
    expect(isSessionEnded(undefined)).toBe(false);
    expect(isSessionEnded(null)).toBe(false);
    expect(isSessionEnded(401)).toBe(false);
  });
});

describe("apiFetch", () => {
  it("leaves for the exit route when the token has expired", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        respond(401, {
          success: false,
          error_code: "TOKEN_EXPIRED",
          message: "Access token has expired.",
        }),
      ),
    );

    await apiFetch("/api/documents");

    expect(replace).toHaveBeenCalledWith(SESSION_ENDED_PATH);
  });

  it("leaves when the token is missing or unusable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        respond(401, { success: false, error_code: "UNAUTHORIZED", message: "Not signed in." }),
      ),
    );

    await apiFetch("/api/documents");

    expect(replace).toHaveBeenCalledWith(SESSION_ENDED_PATH);
  });

  it("still hands the caller a readable body - the clone must not consume it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        respond(401, { success: false, error_code: "TOKEN_EXPIRED", message: "gone" }),
      ),
    );

    const response = await apiFetch("/api/documents");

    await expect(response.json()).resolves.toMatchObject({ message: "gone" });
  });

  it("stays put on a 403 - a valid session on the wrong screen", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => respond(403, { success: false, error_code: "FORBIDDEN", message: "no" })),
    );

    await apiFetch("/api/super-admin/organizations");

    expect(replace).not.toHaveBeenCalled();
  });

  it("stays put on a 401 that is not our envelope", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<html>gateway</html>", { status: 401 })));

    const response = await apiFetch("/api/documents");

    expect(replace).not.toHaveBeenCalled();
    expect(response.status).toBe(401);
  });

  it("stays put on a 401 carrying some other code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        respond(401, { success: false, error_code: "INVALID_CREDENTIALS", message: "no" }),
      ),
    );

    await apiFetch("/api/documents");

    expect(replace).not.toHaveBeenCalled();
  });

  it("passes a success through untouched, parsing nothing", async () => {
    const payload = { success: true, data: { items: [] } };
    const spy = vi.fn(async () => respond(200, payload));
    vi.stubGlobal("fetch", spy);

    const response = await apiFetch("/api/documents", { method: "GET" });

    expect(spy).toHaveBeenCalledWith("/api/documents", { method: "GET" });
    expect(replace).not.toHaveBeenCalled();
    await expect(response.json()).resolves.toEqual(payload);
  });
});
