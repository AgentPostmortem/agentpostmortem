import { NextRequest, NextResponse } from "next/server";
import { createSupabaseAdminClient } from "@/lib/supabase/admin";
import { submitSchema, screenshotUrlsSchema } from "@/lib/schemas/submit";
import { redactPii } from "@/lib/utils/pii";
import { hashEditToken } from "@/lib/utils/hash";

const editSchema = submitSchema.extend({
  screenshotUrls: screenshotUrlsSchema,
});

interface EditablePostRow {
  id: string;
  case_number: string | null;
  title: string;
  prompt: string | null;
  outcome: string;
  damage_level: 1 | 2 | 3 | 4 | 5;
  estimated_cost_usd: number | null;
  screenshot_urls: string[] | null;
  is_anonymous: boolean;
  submitter_handle: string | null;
  status: "pending" | "approved" | "rejected";
  agents: { slug: string | null } | null;
  post_tags: Array<{ tags: { slug: string | null } | null }> | null;
}

interface RouteContext {
  params: { token: string };
}

export async function GET(_req: NextRequest, { params }: RouteContext) {
  try {
    const supabase = createSupabaseAdminClient();
    const tokenHash = hashEditToken(params.token);

    const { data, error } = await supabase
      .from("posts")
      .select(
        `
        id,
        case_number,
        title,
        prompt,
        outcome,
        damage_level,
        estimated_cost_usd,
        screenshot_urls,
        is_anonymous,
        submitter_handle,
        status,
        agents ( slug ),
        post_tags ( tags ( slug ) )
      `,
      )
      .eq("edit_token_hash", tokenHash)
      .maybeSingle();

    if (error) {
      console.error("[posts/edit] fetch error:", error);
      return NextResponse.json(
        { error: "Failed to load submission." },
        { status: 500 },
      );
    }

    const post = data as EditablePostRow | null;

    if (!post) {
      return NextResponse.json(
        { error: "Edit link is invalid." },
        { status: 404 },
      );
    }

    return NextResponse.json({
      post: {
        id: post.id,
        caseNumber: post.case_number,
        title: post.title,
        prompt: post.prompt,
        outcome: post.outcome,
        damageLevel: post.damage_level,
        estimatedCostUsd: post.estimated_cost_usd,
        screenshotUrls: post.screenshot_urls ?? [],
        isAnonymous: post.is_anonymous,
        authorHandle: post.submitter_handle,
        status: post.status,
        agentSlug: post.agents?.slug ?? "",
        tags:
          post.post_tags
            ?.map((row) => row.tags?.slug)
            .filter((value): value is string => Boolean(value)) ?? [],
      },
    });
  } catch (error) {
    console.error("[posts/edit] unexpected fetch error:", error);
    return NextResponse.json(
      { error: "Internal server error." },
      { status: 500 },
    );
  }
}

export async function PATCH(req: NextRequest, { params }: RouteContext) {
  try {
    const parsed = editSchema.safeParse(await req.json());
    if (!parsed.success) {
      return NextResponse.json(
        { error: "Validation failed.", issues: parsed.error.issues },
        { status: 400 },
      );
    }

    const tokenHash = hashEditToken(params.token);
    const supabase = createSupabaseAdminClient();

    const { data: existing, error: existingError } = await supabase
      .from("posts")
      .select("id")
      .eq("edit_token_hash", tokenHash)
      .maybeSingle();

    if (existingError) {
      console.error("[posts/edit] lookup error:", existingError);
      return NextResponse.json(
        { error: "Failed to load submission." },
        { status: 500 },
      );
    }

    if (!existing) {
      return NextResponse.json(
        { error: "Edit link is invalid." },
        { status: 404 },
      );
    }

    const { data } = parsed;
    const { data: agent, error: agentErr } = await supabase
      .from("agents")
      .select("id")
      .eq("slug", data.agentSlug)
      .single();

    if (agentErr || !agent) {
      return NextResponse.json({ error: "Unknown agent." }, { status: 400 });
    }

    const { data: tags, error: tagsErr } = await supabase
      .from("tags")
      .select("id, slug")
      .in("slug", data.tags);

    if (tagsErr) {
      console.error("[posts/edit] tag lookup error:", tagsErr);
      return NextResponse.json(
        { error: "Failed to resolve tags." },
        { status: 500 },
      );
    }

    if (!tags || tags.length !== new Set(data.tags).size) {
      return NextResponse.json(
        { error: "One or more selected tags are invalid." },
        { status: 400 },
      );
    }

    const { error: updateError } = await supabase
      .from("posts")
      .update({
        agent_id: agent.id,
        title: redactPii(data.title),
        prompt: data.prompt ? redactPii(data.prompt) : null,
        outcome: redactPii(data.outcome),
        damage_level: data.damageLevel,
        estimated_cost_usd: data.estimatedCostUsd ?? null,
        screenshot_urls: data.screenshotUrls ?? [],
        is_anonymous: data.isAnonymous,
        submitter_handle:
          !data.isAnonymous && data.authorHandle
            ? redactPii(data.authorHandle)
            : null,
        status: "pending",
      })
      .eq("id", existing.id);

    if (updateError) {
      console.error("[posts/edit] update error:", updateError);
      return NextResponse.json(
        { error: "Failed to update submission." },
        { status: 500 },
      );
    }

    const { error: deleteError } = await supabase
      .from("post_tags")
      .delete()
      .eq("post_id", existing.id);

    if (deleteError) {
      console.error("[posts/edit] tag delete error:", deleteError);
      return NextResponse.json(
        { error: "Failed to update submission tags." },
        { status: 500 },
      );
    }

    const { error: insertError } = await supabase
      .from("post_tags")
      .insert(tags.map((tag) => ({ post_id: existing.id, tag_id: tag.id })));

    if (insertError) {
      console.error("[posts/edit] tag insert error:", insertError);
      return NextResponse.json(
        { error: "Failed to update submission tags." },
        { status: 500 },
      );
    }

    return NextResponse.json({
      message: "Submission updated. It has been returned to moderation review.",
    });
  } catch (error) {
    console.error("[posts/edit] unexpected update error:", error);
    return NextResponse.json(
      { error: "Internal server error." },
      { status: 500 },
    );
  }
}
