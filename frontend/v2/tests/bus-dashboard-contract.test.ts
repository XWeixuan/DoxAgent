import { expect, it } from "vitest";
import { ApiClient } from "../src/core/api";
// Captured from the actual FastAPI dashboard route, including its HTTP Resource.
import response from "./fixtures/bus-dashboard-http.json";

const auth = {
  token: () => "fixture",
  refresh: async () => false,
  invalidate() {},
};
const request = (body: unknown) =>
  new ApiClient(
    auth,
    async () => new Response(JSON.stringify(body), { status: 200 }),
  ).request("BusDashboard", "/tickers/MU/message-bus/dashboard");

it("accepts the complete Python HTTP response through the real API client", async () => {
  const result = await request(response);
  expect(result.data.coverage.state).toBe("COMPLETE");
  expect(result.data.data?.coverage).toEqual({
    expected: 0,
    succeeded: 0,
    ratio: null,
  });
});

it("rejects the old coverage collision and retains the server request id", async () => {
  const invalid = {
    ...response,
    data: { ...response.data, coverage: response.data.data.coverage },
  };
  await expect(request(invalid)).rejects.toMatchObject({
    code: "INVALID_RESPONSE",
    status: 200,
    requestId: response.meta.request_id,
  });
});
