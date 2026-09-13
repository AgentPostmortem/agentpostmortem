import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const from = vi.fn();
const consumeSharedRateLimit = vi.fn();
const hashIp = vi.fn(() => "hashed-ip");
const getClientIp = vi.fn(() => "127.0.0.1");

vi.mock("@/lib/supabase/server", () => ({
  createSupabaseServerClient: () => ({ from }),
}));

vi.mock("@/lib/supabase/admin", () => ({
  createSupabaseAdminClient: () => ({ from }),
}));

vi.mock("@/lib/rate-limit/shared", () => ({
  consumeSharedRateLimit,
}));

vi.mock("@/lib/utils/hash", () => ({
  hashIp,
  getClientIp,
}));

function createRequest(body: unknown) {
  return new NextRequest("http://localhost/api/comments", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("POST /api/comments", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    consumeSharedRateLimit.mockResolvedValue({
      allowed: true,
      remaining: 4,
      resetAt: new Date().toISOString(),
      currentCount: 1,
    });
    from.mockReturnValue({
      insert: vi.fn().mockReturnValue({
        select: () => ({
          single: async () => ({
            data: {
              id: "comment-1",
              body: "This comment has enough content.",
              is_anonymous: false,
              author_handle: "saved-handle",
              created_at: "2026-09-13T00:00:00.000Z",
            },
            error: null,
          }),
        }),
      }),
    });
  });

  it("rejects an author handle longer than 64 characters", async () => {
    const { POST } = await import("./route");
    const response = await POST(
      createRequest({
        post_id: "post-1",
        body: "This comment has enough content.",
        is_anonymous: false,
        author_handle: "a".repeat(65),
      }),
    );

    expect(response.status).toBe(400);
    await expect(response.json()).resolves.toEqual({
      error: "Handle must be 64 characters or fewer.",
    });
    expect(consumeSharedRateLimit).not.toHaveBeenCalled();
    expect(from).not.toHaveBeenCalled();
  });

  it("accepts a 64-character author handle", async () => {
    const handle = "a".repeat(64);
    const insertSpy = vi.fn().mockReturnValue({
      select: () => ({
        single: async () => ({
          data: {
            id: "comment-1",
            body: "This comment has enough content.",
            is_anonymous: false,
            author_handle: handle,
            created_at: "2026-09-13T00:00:00.000Z",
          },
          error: null,
        }),
      }),
    });
    from.mockReturnValue({ insert: insertSpy });

    const { POST } = await import("./route");
    const response = await POST(
      createRequest({
        post_id: "post-1",
        body: "This comment has enough content.",
        is_anonymous: false,
        author_handle: handle,
      }),
    );

    expect(response.status).toBe(201);
    expect(insertSpy).toHaveBeenCalledWith(
      expect.objectContaining({ author_handle: handle }),
    );
  });
});
