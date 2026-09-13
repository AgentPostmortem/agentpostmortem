import { beforeEach, describe, expect, it, vi } from "vitest";

const from = vi.fn();

vi.mock("@/lib/supabase/server", () => ({
  createSupabaseServerClient: () => ({ from }),
}));

describe("post privacy", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
  });

  it("does not expose a legacy handle for anonymous posts", async () => {
    const query = {
      select: vi.fn(),
      eq: vi.fn(),
      single: vi.fn(),
    };
    query.select.mockReturnValue(query);
    query.eq.mockReturnValue(query);
    query.single.mockResolvedValue({
      data: {
        id: "post-1",
        case_number: "APM-0001",
        title: "Anonymous case",
        outcome: "A long enough outcome for the test fixture.",
        damage_level: 3,
        vote_score: 0,
        created_at: "2026-09-01T00:00:00.000Z",
        is_anonymous: true,
        submitter_handle: "private-handle",
        agents: { slug: "claude", name: "Claude" },
        post_tags: [],
      },
      error: null,
    });
    from.mockReturnValue(query);

    const { fetchPostByCase } = await import("./posts");
    const post = await fetchPostByCase("APM-0001");

    expect(post).toMatchObject({
      isAnonymous: true,
      authorHandle: undefined,
    });
  });

  it("keeps the public handle for attributed posts", async () => {
    const query = {
      select: vi.fn(),
      eq: vi.fn(),
      single: vi.fn(),
    };
    query.select.mockReturnValue(query);
    query.eq.mockReturnValue(query);
    query.single.mockResolvedValue({
      data: {
        id: "post-1",
        case_number: "APM-0001",
        title: "Attributed case",
        outcome: "A long enough outcome for the test fixture.",
        damage_level: 3,
        vote_score: 0,
        created_at: "2026-09-01T00:00:00.000Z",
        is_anonymous: false,
        submitter_handle: "ops-team",
        agents: { slug: "claude", name: "Claude" },
        post_tags: [],
      },
      error: null,
    });
    from.mockReturnValue(query);

    const { fetchPostByCase } = await import("./posts");
    const post = await fetchPostByCase("APM-0001");

    expect(post).toMatchObject({
      isAnonymous: false,
      authorHandle: "ops-team",
    });
  });
});

describe("fetchRelatedPosts", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
  });

  it("scopes the no-tag-match fallback to the requested agent", async () => {
    const fallbackQuery = {
      select: vi.fn(),
      eq: vi.fn(),
      neq: vi.fn(),
      order: vi.fn(),
      limit: vi.fn(),
    };
    fallbackQuery.select.mockReturnValue(fallbackQuery);
    fallbackQuery.eq.mockReturnValue(fallbackQuery);
    fallbackQuery.neq.mockReturnValue(fallbackQuery);
    fallbackQuery.order.mockReturnValue(fallbackQuery);
    fallbackQuery.limit.mockResolvedValue({
      data: [
        {
          id: "post-2",
          case_number: "APM-0002",
          title: "Another Claude failure",
          agents: { slug: "claude", name: "Claude" },
          post_tags: [],
        },
      ],
    });

    from.mockImplementation((table: string) => {
      if (table === "tags") {
        return {
          select: () => ({
            in: vi.fn().mockResolvedValue({ data: [{ id: "tag-1" }] }),
          }),
        };
      }

      if (table === "post_tags") {
        return {
          select: () => ({
            in: vi.fn().mockResolvedValue({ data: [] }),
          }),
        };
      }

      if (table === "posts") return fallbackQuery;
      throw new Error(`Unexpected table ${table}`);
    });

    const { fetchRelatedPosts } = await import("./posts");
    const posts = await fetchRelatedPosts(
      "APM-0001",
      "claude",
      ["hallucination"],
      2,
    );

    expect(posts).toHaveLength(1);
    expect(fallbackQuery.select).toHaveBeenCalledWith(
      `*, agents!inner(slug, name, company), post_tags(tags(slug, label))`,
    );
    expect(fallbackQuery.eq).toHaveBeenCalledWith("agents.slug", "claude");
    expect(fallbackQuery.eq).toHaveBeenCalledWith("status", "approved");
    expect(fallbackQuery.neq).toHaveBeenCalledWith("case_number", "APM-0001");
    expect(fallbackQuery.order).toHaveBeenCalledWith("vote_score", {
      ascending: false,
    });
    expect(fallbackQuery.limit).toHaveBeenCalledWith(2);
  });
});
