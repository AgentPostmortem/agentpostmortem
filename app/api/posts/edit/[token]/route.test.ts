import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const from = vi.fn();

vi.mock("@/lib/supabase/admin", () => ({
  createSupabaseAdminClient: () => ({ from }),
}));

function createPatchRequest(body: unknown) {
  return new NextRequest("http://localhost/api/posts/edit/token-123", {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("PATCH /api/posts/edit/[token]", () => {
  const originalEnv = { ...process.env };

  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    process.env.R2_PUBLIC_URL = "https://cdn.example.com";
  });

  afterEach(() => {
    process.env = { ...originalEnv };
  });

  it("rejects a screenshot URL that isn't on our own R2 bucket", async () => {
    from.mockImplementation((table: string) => {
      if (table === "posts") {
        return {
          select: () => ({
            eq: () => ({
              maybeSingle: async () => ({
                data: { id: "post-1" },
                error: null,
              }),
            }),
          }),
        };
      }

      throw new Error(`Unexpected table ${table}`);
    });

    const { PATCH } = await import("./route");
    const response = await PATCH(
      createPatchRequest({
        agentSlug: "claude",
        title: "Agent deleted a customer record during a routine sync",
        outcome:
          "The assistant misunderstood the task, deleted a live customer record, and forced the team into a manual restore that took several hours to unwind safely.",
        damageLevel: 3,
        tags: ["hallucination"],
        isAnonymous: true,
        screenshotUrls: ["https://evil.example.com/pwned.png"],
      }),
      { params: { token: "token-123" } },
    );

    expect(response.status).toBe(400);
  });

  it("returns 404 for an invalid edit token", async () => {
    from.mockImplementation((table: string) => {
      if (table === "posts") {
        return {
          select: () => ({
            eq: () => ({
              maybeSingle: async () => ({ data: null, error: null }),
            }),
          }),
        };
      }

      throw new Error(`Unexpected table ${table}`);
    });

    const { PATCH } = await import("./route");
    const response = await PATCH(
      createPatchRequest({
        agentSlug: "claude",
        title: "Agent deleted a customer record during a routine sync",
        outcome:
          "The assistant misunderstood the task, deleted a live customer record, and forced the team into a manual restore that took several hours to unwind safely.",
        damageLevel: 3,
        tags: ["hallucination"],
        isAnonymous: true,
      }),
      { params: { token: "token-123" } },
    );

    expect(response.status).toBe(404);
  });

  it("updates the post and rewrites its tags", async () => {
    const updateEq = vi.fn().mockResolvedValue({ error: null });
    const updateSpy = vi.fn().mockReturnValue({ eq: updateEq });
    const deleteEq = vi.fn().mockResolvedValue({ error: null });
    const insertTags = vi.fn().mockResolvedValue({ error: null });

    from.mockImplementation((table: string) => {
      if (table === "posts") {
        return {
          select: () => ({
            eq: () => ({
              maybeSingle: async () => ({
                data: { id: "post-1" },
                error: null,
              }),
              single: async () => ({ data: { id: "agent-1" }, error: null }),
            }),
          }),
          update: updateSpy,
        };
      }

      if (table === "agents") {
        return {
          select: () => ({
            eq: () => ({
              single: async () => ({ data: { id: "agent-1" }, error: null }),
            }),
          }),
        };
      }

      if (table === "tags") {
        return {
          select: () => ({
            in: async () => ({
              data: [{ id: "tag-1", slug: "hallucination" }],
              error: null,
            }),
          }),
        };
      }

      if (table === "post_tags") {
        return {
          delete: () => ({
            eq: deleteEq,
          }),
          insert: insertTags,
        };
      }

      throw new Error(`Unexpected table ${table}`);
    });

    const { PATCH } = await import("./route");
    const response = await PATCH(
      createPatchRequest({
        agentSlug: "claude",
        title: "Agent deleted a customer record during a routine sync",
        outcome:
          "The assistant misunderstood the task, deleted a live customer record, and forced the team into a manual restore that took several hours to unwind safely.",
        damageLevel: 3,
        tags: ["hallucination"],
        isAnonymous: false,
        authorHandle: "ops-team",
        screenshotUrls: [
          "https://cdn.example.com/screenshots/8f14e45f-ceea-467e-b7d1-3cfa78f5c15e.png",
        ],
      }),
      { params: { token: "token-123" } },
    );

    expect(response.status).toBe(200);
    expect(updateSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        is_anonymous: false,
        submitter_handle: "ops-team",
      }),
    );
    expect(updateEq).toHaveBeenCalledWith("id", "post-1");
    expect(deleteEq).toHaveBeenCalledWith("post_id", "post-1");
    expect(insertTags).toHaveBeenCalledWith([
      { post_id: "post-1", tag_id: "tag-1" },
    ]);
  });

  it("clears an author handle when an anonymous post is edited", async () => {
    const updateEq = vi.fn().mockResolvedValue({ error: null });
    const updateSpy = vi.fn().mockReturnValue({ eq: updateEq });
    const deleteEq = vi.fn().mockResolvedValue({ error: null });
    const insertTags = vi.fn().mockResolvedValue({ error: null });

    from.mockImplementation((table: string) => {
      if (table === "posts") {
        return {
          select: () => ({
            eq: () => ({
              maybeSingle: async () => ({
                data: { id: "post-1" },
                error: null,
              }),
            }),
          }),
          update: updateSpy,
        };
      }

      if (table === "agents") {
        return {
          select: () => ({
            eq: () => ({
              single: async () => ({ data: { id: "agent-1" }, error: null }),
            }),
          }),
        };
      }

      if (table === "tags") {
        return {
          select: () => ({
            in: async () => ({
              data: [{ id: "tag-1", slug: "hallucination" }],
              error: null,
            }),
          }),
        };
      }

      if (table === "post_tags") {
        return {
          delete: () => ({
            eq: deleteEq,
          }),
          insert: insertTags,
        };
      }

      throw new Error(`Unexpected table ${table}`);
    });

    const { PATCH } = await import("./route");
    const response = await PATCH(
      createPatchRequest({
        agentSlug: "claude",
        title: "Agent deleted a customer record during a routine sync",
        outcome:
          "The assistant misunderstood the task, deleted a live customer record, and forced the team into a manual restore that took several hours to unwind safely.",
        damageLevel: 3,
        tags: ["hallucination"],
        isAnonymous: true,
        authorHandle: "private-handle",
      }),
      { params: { token: "token-123" } },
    );

    expect(response.status).toBe(200);
    expect(updateSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        is_anonymous: true,
        submitter_handle: null,
      }),
    );
    expect(updateEq).toHaveBeenCalledWith("id", "post-1");
  });

  it("rejects a well-formed R2 URL with a malformed object key", async () => {
    from.mockImplementation((table: string) => {
      if (table === "posts") {
        return {
          select: () => ({
            eq: () => ({
              maybeSingle: async () => ({
                data: { id: "post-1" },
                error: null,
              }),
            }),
          }),
        };
      }

      throw new Error(`Unexpected table ${table}`);
    });

    const { PATCH } = await import("./route");
    const response = await PATCH(
      createPatchRequest({
        agentSlug: "claude",
        title: "Agent deleted a customer record during a routine sync",
        outcome:
          "The assistant misunderstood the task, deleted a live customer record, and forced the team into a manual restore that took several hours to unwind safely.",
        damageLevel: 3,
        tags: ["hallucination"],
        isAnonymous: true,
        screenshotUrls: ["https://cdn.example.com/screenshots/not-a-uuid.png"],
      }),
      { params: { token: "token-123" } },
    );

    expect(response.status).toBe(400);
  });
});
