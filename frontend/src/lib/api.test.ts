import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, publicApi } from "@/lib/api";

function respond(status: number, body: unknown) {
  // A new Response per call: a body can only be read once.
  return vi.fn().mockImplementation(async () => new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
}

afterEach(() => vi.unstubAllGlobals());

describe("api", () => {
  it("sends the session cookie and the CSRF header, and parses JSON", async () => {
    const fetchMock = respond(200, { status: "ok" });
    vi.stubGlobal("fetch", fetchMock);
    await expect(api("/health")).resolves.toEqual({ status: "ok" });
    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/health");
    expect(init.credentials).toBe("include");
    expect((init.headers as Record<string, string>)["X-Requested-With"]).toBe("LegacyLift");
  });

  it("sends a JSON body with its content type", async () => {
    const fetchMock = respond(201, { received: true });
    vi.stubGlobal("fetch", fetchMock);
    await publicApi.lead({ kind: "contact", email: "a@b.co" });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({ kind: "contact", email: "a@b.co" });
  });

  it("turns the API's error format into an ApiError with its code and fields", async () => {
    vi.stubGlobal("fetch", respond(422, { detail: [], error: { code: "validation_error", message: "The request is not valid", status: 422, fields: [{ field: "body.email", message: "bad" }] } }));
    const error = await api("/x").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, code: "validation_error", message: "The request is not valid", fields: [{ field: "body.email", message: "bad" }] });
  });

  it("falls back to the plain detail of an older error", async () => {
    vi.stubGlobal("fetch", respond(409, { detail: "Already there" }));
    await expect(api("/x")).rejects.toMatchObject({ status: 409, message: "Already there" });
  });

  it("copes with an error that is not JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => new Response("<html>bad gateway</html>", { status: 502 })));
    await expect(api("/x")).rejects.toMatchObject({ status: 502, code: "http_502" });
  });

  it("reports an unreachable server in plain words", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api("/x")).rejects.toMatchObject({ status: 0, code: "network_error", message: expect.stringContaining("could not be reached") });
  });

  it("returns nothing for 204", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => new Response(null, { status: 204 })));
    await expect(api("/x", { method: "POST" })).resolves.toBeUndefined();
  });

  it("builds the estimate address from a plan and a whole number of records", async () => {
    const fetchMock = respond(200, {});
    vi.stubGlobal("fetch", fetchMock);
    await publicApi.estimate("professional", 2_000_000.9);
    expect((fetchMock.mock.calls[0] as [string])[0]).toBe("/api/v1/public/pricing/estimate?plan=professional&records=2000000");
    await publicApi.estimate("starter", -5);
    expect((fetchMock.mock.calls[1] as [string])[0]).toBe("/api/v1/public/pricing/estimate?plan=starter&records=0");
  });
});
