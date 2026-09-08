import { createSupabaseServerClient } from "@/lib/supabase/server";
import type { Post } from "@/types";
import { isSeverityLevel, type SeverityLevel } from "@/lib/constants/severity";
import { incidentDate, incidentMonth } from "@/lib/utils/incident-date";
import {
  bucketByYear,
  yearRange,
  type YearBucket,
} from "@/lib/utils/feed-year";

export type FeedTab = "hot" | "new" | "week" | "hof";

function rowToPost(row: Record<string, unknown>): Post {
  const agent = row.agents as Record<string, unknown> | null;
  const postTags = row.post_tags as Array<{ tags: { slug: string } }> | null;

  return {
    id: row.id as string,
    caseNumber: row.case_number as string,
    title: row.title as string,
    agentSlug: (agent?.slug as string) ?? "other",
    agentName: (agent?.name as string) ?? "Unknown",
    outcome: row.outcome as string,
    prompt: (row.prompt as string | null) ?? undefined,
    damageLevel: row.damage_level as 1 | 2 | 3 | 4 | 5,
    estimatedCostUsd: (row.estimated_cost_usd as number | null) ?? null,
    tags: postTags?.map((pt) => pt.tags.slug) ?? [],
    voteScore: (row.vote_score as number) ?? 0,
    createdAt: row.created_at as string,
    isAnonymous: (row.is_anonymous as boolean) ?? true,
    authorHandle: (row.submitter_handle as string | null) ?? undefined,
    screenshots: (row.screenshot_urls as string[] | null) ?? [],
    sourceUrl: (row.source_url as string | null) ?? undefined,
    sourceTitle: (row.source_title as string | null) ?? undefined,
    sourcePublishedAt: (row.source_published_at as string | null) ?? undefined,
    verifiedFacts: (row.verified_facts as string[] | null) ?? [],
    unknowns: (row.unknowns as string[] | null) ?? [],
    lessons: (row.lessons as string[] | null) ?? [],
  };
}

const PAGE_SIZE = 20;

export async function fetchFeedPosts(
  tab: FeedTab,
  page = 1,
  agentSlug?: string,
  severity?: number,
  year?: string,
): Promise<{ posts: Post[]; total: number }> {
  try {
    const supabase = createSupabaseServerClient();
    const from = (page - 1) * PAGE_SIZE;
    const to = from + PAGE_SIZE - 1;

    let query = supabase
      .from("posts")
      .select(
        `*, agents!inner(slug, name, company), post_tags(tags(slug, label))`,
        { count: "exact" },
      )
      .eq("status", "approved")
      .range(from, to);

    if (agentSlug) query = query.eq("agents.slug", agentSlug);
    // Only filter on a level the column can actually hold. An out of range
    // severity in the URL used to be passed straight through.
    if (severity != null && isSeverityLevel(severity)) {
      query = query.eq("damage_level", severity);
    }

    if (year) {
      // Match incidentDate(): the source publication date decides the year,
      // and only rows without one fall back to the record date.
      const { start, end } = yearRange(year);
      query = query.or(
        [
          `and(source_published_at.gte.${start},source_published_at.lt.${end})`,
          `and(source_published_at.is.null,created_at.gte.${start},created_at.lt.${end})`,
        ].join(","),
      );
    }

    if (tab === "new") {
      // Newest incident first, falling back to the record date for the
      // handful of cases with no known source publication date.
      query = query
        .order("source_published_at", { ascending: false, nullsFirst: false })
        .order("created_at", { ascending: false });
    } else if (tab === "week") {
      const weekAgo = new Date(
        Date.now() - 7 * 24 * 60 * 60 * 1000,
      ).toISOString();
      query = query
        .gte("created_at", weekAgo)
        .order("vote_score", { ascending: false });
    } else if (tab === "hof") {
      query = query.order("vote_score", { ascending: false });
    } else {
      query = query
        .order("vote_score", { ascending: false })
        .order("source_published_at", { ascending: false, nullsFirst: false })
        .order("created_at", { ascending: false });
    }

    const { data, error, count } = await query;
    if (error || !data) return { posts: [], total: 0 };
    return {
      posts: data.map((row) => rowToPost(row as Record<string, unknown>)),
      total: count ?? 0,
    };
  } catch {
    return { posts: [], total: 0 };
  }
}

export async function fetchPostByCase(
  caseNumber: string,
): Promise<Post | null> {
  try {
    const supabase = createSupabaseServerClient();
    const { data, error } = await supabase
      .from("posts")
      .select(`*, agents(slug, name, company), post_tags(tags(slug, label))`)
      .eq("case_number", caseNumber.toUpperCase())
      .eq("status", "approved")
      .single();

    if (error || !data) return null;
    return rowToPost(data as Record<string, unknown>);
  } catch {
    return null;
  }
}

export async function fetchPostsByAgent(
  slug: string,
  limit = 20,
): Promise<Post[]> {
  try {
    const supabase = createSupabaseServerClient();
    const { data, error } = await supabase
      .from("posts")
      .select(
        `*, agents!inner(slug, name, company), post_tags(tags(slug, label))`,
      )
      .eq("agents.slug", slug)
      .eq("status", "approved")
      .order("vote_score", { ascending: false })
      .limit(limit);

    if (error || !data) return [];
    return data.map((row) => rowToPost(row as Record<string, unknown>));
  } catch {
    return [];
  }
}

export async function fetchPostsByTag(
  tagSlug: string,
  limit = 20,
): Promise<Post[]> {
  try {
    const supabase = createSupabaseServerClient();
    const { data, error } = await supabase
      .from("posts")
      .select(
        `*, agents(slug, name, company), post_tags!inner(tags!inner(slug, label))`,
      )
      .eq("post_tags.tags.slug", tagSlug)
      .eq("status", "approved")
      .order("vote_score", { ascending: false })
      .limit(limit);

    if (error || !data) return [];
    return data.map((row) => rowToPost(row as Record<string, unknown>));
  } catch {
    return [];
  }
}

export async function fetchSiteStats(): Promise<{
  totalPosts: number;
  totalAgents: number;
  totalDamage: number;
}> {
  try {
    const supabase = createSupabaseServerClient();
    const { data } = await supabase
      .from("posts")
      .select("estimated_cost_usd, agent_id")
      .eq("status", "approved");

    if (!data) return { totalPosts: 0, totalAgents: 0, totalDamage: 0 };

    const rows = data as Array<{
      estimated_cost_usd: number | null;
      agent_id: string;
    }>;
    const totalDamage = rows.reduce(
      (sum, p) => sum + (p.estimated_cost_usd ?? 0),
      0,
    );
    const uniqueAgents = new Set(rows.map((p) => p.agent_id)).size;

    return { totalPosts: data.length, totalAgents: uniqueAgents, totalDamage };
  } catch {
    return { totalPosts: 0, totalAgents: 0, totalDamage: 0 };
  }
}

export async function fetchRelatedPosts(
  caseNumber: string,
  agentSlug: string,
  tags: string[],
  limit = 3,
): Promise<Post[]> {
  try {
    const supabase = createSupabaseServerClient();

    // Prefer tag-based related posts
    if (tags.length > 0) {
      const { data: tagRows } = await supabase
        .from("tags")
        .select("id")
        .in("slug", tags);
      const tagIds = (tagRows ?? []).map((t) => (t as { id: string }).id);

      if (tagIds.length > 0) {
        const { data: postTagRows } = await supabase
          .from("post_tags")
          .select("post_id")
          .in("tag_id", tagIds);

        const candidateIds = Array.from(
          new Set(
            (postTagRows ?? []).map((r) => (r as { post_id: string }).post_id),
          ),
        );

        if (candidateIds.length > 0) {
          const { data } = await supabase
            .from("posts")
            .select(
              `*, agents(slug, name, company), post_tags(tags(slug, label))`,
            )
            .eq("status", "approved")
            .neq("case_number", caseNumber)
            .in("id", candidateIds)
            .order("vote_score", { ascending: false })
            .limit(limit);

          if (data && data.length > 0) {
            return data.map((row) => rowToPost(row as Record<string, unknown>));
          }
        }
      }
    }

    // Fallback: same agent
    const { data } = await supabase
      .from("posts")
      .select(
        `*, agents!inner(slug, name, company), post_tags(tags(slug, label))`,
      )
      .eq("agents.slug", agentSlug)
      .eq("status", "approved")
      .neq("case_number", caseNumber)
      .order("vote_score", { ascending: false })
      .limit(limit);

    return (data ?? []).map((row) => rowToPost(row as Record<string, unknown>));
  } catch {
    return [];
  }
}

export interface SearchFilters {
  agentSlug?: string;
  minSeverity?: number;
  maxSeverity?: number;
}

export async function fetchSearchPosts(
  query: string,
  filters: SearchFilters = {},
  limit = 20,
): Promise<Post[]> {
  try {
    const supabase = createSupabaseServerClient();

    // Find agent IDs whose name matches the query (only when no explicit agent filter)
    let agentIds: string[] = [];
    if (!filters.agentSlug) {
      const { data: agentRows } = await supabase
        .from("agents")
        .select("id")
        .ilike("name", `%${query}%`);
      agentIds = (agentRows ?? []).map((a) => (a as { id: string }).id);
    }

    const textFilter = `title.ilike.%${query}%,outcome.ilike.%${query}%,case_number.ilike.%${query}%`;
    const orFilter =
      agentIds.length > 0
        ? `${textFilter},agent_id.in.(${agentIds.join(",")})`
        : textFilter;

    let q = supabase
      .from("posts")
      .select(`*, agents(slug, name, company), post_tags(tags(slug, label))`)
      .eq("status", "approved")
      .or(orFilter);

    if (filters.agentSlug) {
      // Join via agents slug — need a subquery approach: filter by agents(slug)
      const { data: agentRow } = await supabase
        .from("agents")
        .select("id")
        .eq("slug", filters.agentSlug)
        .single();
      if (agentRow) {
        q = q.eq("agent_id", (agentRow as { id: string }).id);
      }
    }

    if (filters.minSeverity != null) {
      q = q.gte("damage_level", filters.minSeverity);
    }
    if (filters.maxSeverity != null) {
      q = q.lte("damage_level", filters.maxSeverity);
    }

    const { data, error } = await q
      .order("vote_score", { ascending: false })
      .limit(limit);

    if (error || !data) return [];
    return data.map((row) => rowToPost(row as Record<string, unknown>));
  } catch {
    return [];
  }
}

export async function fetchAgentCaseCounts(): Promise<Record<string, number>> {
  try {
    const supabase = createSupabaseServerClient();
    const { data } = await supabase
      .from("posts")
      .select("agents!inner(slug)")
      .eq("status", "approved");

    const counts: Record<string, number> = {};
    for (const row of (data ?? []) as Array<{
      agents: { slug: string } | null;
    }>) {
      const slug = row.agents?.slug;
      if (slug) counts[slug] = (counts[slug] ?? 0) + 1;
    }
    return counts;
  } catch {
    return {};
  }
}

export async function fetchTagCaseCounts(): Promise<Record<string, number>> {
  try {
    const supabase = createSupabaseServerClient();
    const { data } = await supabase
      .from("post_tags")
      .select("tags!inner(slug), posts!inner(status)")
      .eq("posts.status", "approved");

    const counts: Record<string, number> = {};
    for (const row of (data ?? []) as Array<{
      tags: { slug: string } | null;
    }>) {
      const slug = row.tags?.slug;
      if (slug) counts[slug] = (counts[slug] ?? 0) + 1;
    }
    return counts;
  } catch {
    return {};
  }
}

export async function fetchCommentCountsByPostIds(
  postIds: string[],
): Promise<Record<string, number>> {
  if (postIds.length === 0) return {};
  try {
    const supabase = createSupabaseServerClient();
    const { data } = await supabase
      .from("comments")
      .select("post_id")
      .in("post_id", postIds)
      .eq("status", "visible");

    const counts: Record<string, number> = {};
    for (const row of (data ?? []) as Array<{ post_id: string }>) {
      counts[row.post_id] = (counts[row.post_id] ?? 0) + 1;
    }
    return counts;
  } catch {
    return {};
  }
}

export interface StatsData {
  totalCases: number;
  totalDamageUsd: number;
  bySeverity: Record<number, number>;
  byAgent: Array<{ name: string; slug: string; count: number }>;
  byTag: Array<{ slug: string; label: string; count: number }>;
  recentByMonth: Array<{ month: string; count: number }>;
}

export async function fetchStatsData(): Promise<StatsData> {
  try {
    const supabase = createSupabaseServerClient();

    const { data: posts } = await supabase
      .from("posts")
      .select(
        "damage_level, estimated_cost_usd, created_at, source_published_at, agents(slug, name), post_tags(tags(slug, label))",
      )
      .eq("status", "approved")
      .order("created_at", { ascending: false });

    const rows = (posts ?? []) as Array<{
      damage_level: number;
      estimated_cost_usd: number | null;
      created_at: string;
      source_published_at: string | null;
      agents: { slug: string; name: string } | null;
      post_tags: Array<{ tags: { slug: string; label: string } | null }>;
    }>;

    const totalDamageUsd = rows.reduce(
      (sum, r) => sum + (r.estimated_cost_usd ?? 0),
      0,
    );

    const bySeverity: Record<number, number> = { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 };
    const agentMap = new Map<string, { name: string; count: number }>();
    const tagMap = new Map<string, { label: string; count: number }>();
    const monthMap = new Map<string, number>();

    for (const row of rows) {
      bySeverity[row.damage_level] = (bySeverity[row.damage_level] ?? 0) + 1;

      if (row.agents) {
        const existing = agentMap.get(row.agents.slug);
        agentMap.set(row.agents.slug, {
          name: row.agents.name,
          count: (existing?.count ?? 0) + 1,
        });
      }

      for (const pt of row.post_tags ?? []) {
        if (pt.tags) {
          const existing = tagMap.get(pt.tags.slug);
          tagMap.set(pt.tags.slug, {
            label: pt.tags.label,
            count: (existing?.count ?? 0) + 1,
          });
        }
      }

      const month = incidentMonth(row); // "YYYY-MM", source date preferred
      if (month) monthMap.set(month, (monthMap.get(month) ?? 0) + 1);
    }

    const byAgent = Array.from(agentMap.entries())
      .map(([slug, { name, count }]) => ({ slug, name, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 10);

    const byTag = Array.from(tagMap.entries())
      .map(([slug, { label, count }]) => ({ slug, label, count }))
      .sort((a, b) => b.count - a.count)
      .slice(0, 12);

    const recentByMonth = Array.from(monthMap.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .slice(-12)
      .map(([month, count]) => ({ month, count }));

    return {
      totalCases: rows.length,
      totalDamageUsd,
      bySeverity,
      byAgent,
      byTag,
      recentByMonth,
    };
  } catch {
    return {
      totalCases: 0,
      totalDamageUsd: 0,
      bySeverity: { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 },
      byAgent: [],
      byTag: [],
      recentByMonth: [],
    };
  }
}

export interface OverviewBucket {
  slug: string;
  label: string;
  count: number;
}

export interface AgentBucket extends OverviewBucket {
  /** Cases at damage level 4 or 5 */
  severeCount: number;
  /** Sum of estimated_cost_usd for this agent, 0 when nothing is quantified */
  damageUsd: number;
}

export interface MonthBucket {
  month: string;
  count: number;
  damageUsd: number;
}

/**
 * Read-only homepage aggregate. One query over approved posts, folded into the
 * counts the registry overview renders. Every number here is computed from real
 * rows; nothing is estimated or filled in.
 */
export interface RegistryOverview {
  totalCases: number;
  totalDamageUsd: number;
  /** Cases that carry an estimated_cost_usd value */
  quantifiedCases: number;
  bySeverity: Record<SeverityLevel, number>;
  byAgent: AgentBucket[];
  byTag: OverviewBucket[];
  byMonth: MonthBucket[];
  /** Case counts per incident year, newest first */
  byYear: YearBucket[];
  /** Newest case timestamp on file, ISO string */
  latestIncidentAt: string | null;
}

const EMPTY_OVERVIEW: RegistryOverview = {
  totalCases: 0,
  totalDamageUsd: 0,
  quantifiedCases: 0,
  bySeverity: { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 },
  byAgent: [],
  byTag: [],
  byMonth: [],
  byYear: [],
  latestIncidentAt: null,
};

export async function fetchRegistryOverview(): Promise<RegistryOverview> {
  try {
    const supabase = createSupabaseServerClient();
    const { data } = await supabase
      .from("posts")
      .select(
        "damage_level, estimated_cost_usd, created_at, source_published_at, agents(slug, name), post_tags(tags(slug, label))",
      )
      .eq("status", "approved")
      .order("created_at", { ascending: false });

    const rows = (data ?? []) as Array<{
      damage_level: number;
      estimated_cost_usd: number | null;
      created_at: string;
      source_published_at: string | null;
      agents: { slug: string; name: string } | null;
      post_tags: Array<{ tags: { slug: string; label: string } | null }>;
    }>;

    if (rows.length === 0) return EMPTY_OVERVIEW;

    const bySeverity: Record<SeverityLevel, number> = {
      1: 0,
      2: 0,
      3: 0,
      4: 0,
      5: 0,
    };
    const agentMap = new Map<string, AgentBucket>();
    const tagMap = new Map<string, OverviewBucket>();
    const monthMap = new Map<string, MonthBucket>();

    let totalDamageUsd = 0;
    let quantifiedCases = 0;

    for (const row of rows) {
      const level = (row.damage_level ?? 1) as SeverityLevel;
      bySeverity[level] = (bySeverity[level] ?? 0) + 1;

      const cost = row.estimated_cost_usd ?? 0;
      totalDamageUsd += cost;
      if (row.estimated_cost_usd != null) quantifiedCases += 1;

      if (row.agents) {
        const bucket = agentMap.get(row.agents.slug) ?? {
          slug: row.agents.slug,
          label: row.agents.name,
          count: 0,
          severeCount: 0,
          damageUsd: 0,
        };
        bucket.count += 1;
        if (level >= 4) bucket.severeCount += 1;
        bucket.damageUsd += cost;
        agentMap.set(row.agents.slug, bucket);
      }

      for (const pt of row.post_tags ?? []) {
        if (!pt.tags) continue;
        const bucket = tagMap.get(pt.tags.slug) ?? {
          slug: pt.tags.slug,
          label: pt.tags.label,
          count: 0,
        };
        bucket.count += 1;
        tagMap.set(pt.tags.slug, bucket);
      }

      const month = incidentMonth(row) ?? row.created_at.slice(0, 7);
      const mb = monthMap.get(month) ?? { month, count: 0, damageUsd: 0 };
      mb.count += 1;
      mb.damageUsd += cost;
      monthMap.set(month, mb);
    }

    return {
      totalCases: rows.length,
      totalDamageUsd,
      quantifiedCases,
      bySeverity,
      byAgent: Array.from(agentMap.values()).sort((a, b) => b.count - a.count),
      byTag: Array.from(tagMap.values()).sort((a, b) => b.count - a.count),
      byMonth: Array.from(monthMap.values())
        .sort((a, b) => a.month.localeCompare(b.month))
        .slice(-12),
      byYear: bucketByYear(rows),
      latestIncidentAt: rows.reduce<string | null>((latest, row) => {
        const date = incidentDate(row);
        return latest == null || date > latest ? date : latest;
      }, null),
    };
  } catch {
    return EMPTY_OVERVIEW;
  }
}

/**
 * The single worst case on file: highest severity, then highest quantified
 * damage, then most recent. Read-only, used for the featured slot.
 */
export async function fetchWorstCase(): Promise<Post | null> {
  try {
    const supabase = createSupabaseServerClient();
    const { data, error } = await supabase
      .from("posts")
      .select(`*, agents(slug, name, company), post_tags(tags(slug, label))`)
      .eq("status", "approved")
      .order("damage_level", { ascending: false })
      .order("estimated_cost_usd", { ascending: false, nullsFirst: false })
      .order("created_at", { ascending: false })
      .limit(1);

    if (error || !data || data.length === 0) return null;
    return rowToPost(data[0] as Record<string, unknown>);
  } catch {
    return null;
  }
}
