import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const hashIp = vi.fn(() => "hashed-ip");
const getClientIp = vi.fn(() => "127.0.0.1");
const consumeSharedRateLimit = vi.fn(async () => ({
  allowed: true,
  remaining: 9,
  resetAt: new Date().toISOString(),
  currentCount: 1,
}));
const getPresignedUploadUrl = vi.fn(async () => ({
  uploadUrl: "https://upload.example/signed",
  publicUrl: "https://cdn.example/screenshots/test.png",
  key: "screenshots/test.png",
}));

vi.mock("@/lib/utils/hash", () => ({
  hashIp,
  getClientIp,
}));

vi.mock("@/lib/rate-limit/shared", () => ({
  consumeSharedRateLimit,
}));

vi.mock("@/lib/observability/events", () => ({
  logEvent: vi.fn(),
}));

vi.mock("@/lib/r2/upload", () => ({
  getPresignedUploadUrl,
}));

function createRequest(body: unknown) {
  return new NextRequest("http://localhost/api/upload/presign", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("POST /api/upload/presign", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    consumeSharedRateLimit.mockResolvedValue({
      allowed: true,
      remaining: 9,
      resetAt: new Date().toISOString(),
      currentCount: 1,
    });
  });

  it("returns 400 if size is missing from request body", async () => {
    const { POST } = await import("./route");
    const response = await POST(
      createRequest({
        filename: "screenshot.png",
        contentType: "image/png",
      }),
    );

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBeDefined();
    expect(getPresignedUploadUrl).not.toHaveBeenCalled();
  });

  it("returns 400 if size exceeds 5 MB limit", async () => {
    const { POST } = await import("./route");
    const response = await POST(
      createRequest({
        filename: "large.png",
        contentType: "image/png",
        size: 6 * 1024 * 1024,
      }),
    );

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("File must be under 5 MB.");
    expect(getPresignedUploadUrl).not.toHaveBeenCalled();
  });

  it("returns 400 if size is 0 or negative", async () => {
    const { POST } = await import("./route");
    const responseZero = await POST(
      createRequest({
        filename: "zero.png",
        contentType: "image/png",
        size: 0,
      }),
    );
    expect(responseZero.status).toBe(400);

    const responseNegative = await POST(
      createRequest({
        filename: "negative.png",
        contentType: "image/png",
        size: -500,
      }),
    );
    expect(responseNegative.status).toBe(400);
    expect(getPresignedUploadUrl).not.toHaveBeenCalled();
  });

  it("returns 400 if size is not an integer", async () => {
    const { POST } = await import("./route");
    const response = await POST(
      createRequest({
        filename: "float.png",
        contentType: "image/png",
        size: 1024.5,
      }),
    );
    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("File size must be an integer byte count.");
    expect(getPresignedUploadUrl).not.toHaveBeenCalled();
  });

  it("allows exactly 5 MB upload size", async () => {
    const { POST } = await import("./route");
    const exact5MB = 5 * 1024 * 1024;
    const response = await POST(
      createRequest({
        filename: "exact5mb.png",
        contentType: "image/png",
        size: exact5MB,
      }),
    );

    expect(response.status).toBe(200);
    expect(getPresignedUploadUrl).toHaveBeenCalledWith(
      "exact5mb.png",
      "image/png",
      "screenshots",
      exact5MB,
    );
  });

  it("generates presigned upload URL with ContentLength when valid size is provided", async () => {
    const { POST } = await import("./route");
    const response = await POST(
      createRequest({
        filename: "valid.png",
        contentType: "image/png",
        size: 2 * 1024 * 1024,
      }),
    );

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.url).toBe("https://upload.example/signed");
    expect(getPresignedUploadUrl).toHaveBeenCalledWith(
      "valid.png",
      "image/png",
      "screenshots",
      2 * 1024 * 1024,
    );
  });

  it("returns 429 when rate limit is exceeded", async () => {
    consumeSharedRateLimit.mockResolvedValue({
      allowed: false,
      remaining: 0,
      resetAt: new Date().toISOString(),
      currentCount: 11,
    });

    const { POST } = await import("./route");
    const response = await POST(
      createRequest({
        filename: "valid.png",
        contentType: "image/png",
        size: 1024,
      }),
    );

    expect(response.status).toBe(429);
    expect(getPresignedUploadUrl).not.toHaveBeenCalled();
  });
});
