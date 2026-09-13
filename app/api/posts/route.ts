import { NextRequest, NextResponse } from "next/server";
import { submitSchema, screenshotUrlsSchema } from "@/lib/schemas/submit";
import { createSupabaseAdminClient } from "@/lib/supabase/admin";
import { sendEditTokenEmail } from "@/lib/resend/send";
import { hashIp, getClientIp, hashEditToken } from "@/lib/utils/hash";
import { redactPii } from "@/lib/utils/pii";
import { randomBytes } from "crypto";
import { logEvent } from "@/lib/observability/events";
import { consumeSharedRateLimit } from "@/lib/rate-limit/shared";

const WINDOW_SECONDS = 60 * 60;
const MAX_SUBMISSIONS = 3;

const bodySchema = submitSchema.extend({
  screenshotUrls: screenshotUrlsSchema,
});

export async function POST(req: NextRequest) {
  try {
    const ip = getClientIp(req.headers);
    const ipHash = hashIp(ip);

    const rateLimit = await consumeSharedRateLimit(
      `posts:${ipHash}`,
      WINDOW_SECONDS,
      MAX_SUBMISSIONS,
    );

    if (!rateLimit.allowed) {
      logEvent({
        event: "submit.rate_limited",
        level: "warn",
        ipHash,
        remaining: rateLimit.remaining,
        resetAt: rateLimit.resetAt,
      });
      return NextResponse.json(
        {
          error: "Too many submissions. You can submit up to 3 cases per hour.",
        },
        { status: 429 },
      );
    }

    const body: unknown = await req.json();
    const parsed = bodySchema.safeParse(body);
    if (!parsed.success) {
      return NextResponse.json(
        { error: "Validation failed.", issues: parsed.error.issues },
        { status: 400 },
      );
    }

    const data = parsed.data;
    const supabase = createSupabaseAdminClient();
    let editLinkSent = false;

    // Resolve agent_id from slug
    const { data: agent, error: agentErr } = await supabase
      .from("agents")
      .select("id")
      .eq("slug", data.agentSlug)
      .single();

    if (agentErr || !agent) {
      return NextResponse.json({ error: "Unknown agent." }, { status: 400 });
    }

    // Resolve tag IDs
    const { data: tags, error: tagsErr } = await supabase
      .from("tags")
      .select("id, slug")
      .in("slug", data.tags);

    if (tagsErr) {
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

    // Redact PII from user-submitted text
    const cleanTitle = redactPii(data.title);
    const cleanOutcome = redactPii(data.outcome);
    const cleanPrompt = data.prompt ? redactPii(data.prompt) : null;
    const cleanHandle =
      !data.isAnonymous && data.authorHandle
        ? redactPii(data.authorHandle)
        : null;

    // Generate edit token — raw token sent to user, hash stored in DB
    const rawToken = randomBytes(32).toString("hex");
    const tokenHash = hashEditToken(rawToken);

    // Insert post (status = pending, goes to moderation queue)
    const { data: post, error: postErr } = await supabase
      .from("posts")
      .insert({
        agent_id: agent.id,
        title: cleanTitle,
        prompt: cleanPrompt,
        outcome: cleanOutcome,
        damage_level: data.damageLevel,
        estimated_cost_usd: data.estimatedCostUsd ?? null,
        screenshot_urls: data.screenshotUrls ?? [],
        is_anonymous: data.isAnonymous,
        submitter_handle: cleanHandle,
        submitter_email: null,
        edit_token_hash: tokenHash,
        ip_hash: ipHash,
        status: "pending",
        vote_score: 0,
      })
      .select("id")
      .single();

    if (postErr || !post) {
      console.error("[posts] insert error:", postErr);
      logEvent({
        event: "submit.persist_failed",
        level: "error",
        ipHash,
        error: postErr?.message ?? "unknown",
      });
      return NextResponse.json(
        { error: "Failed to save submission." },
        { status: 500 },
      );
    }

    // Insert post_tags junction rows
    if (tags && tags.length > 0) {
      const { error: tagLinkErr } = await supabase
        .from("post_tags")
        .insert(tags.map((t) => ({ post_id: post.id, tag_id: t.id })));

      if (tagLinkErr) {
        console.error("[posts] tag link error:", tagLinkErr);
      }
    }

    // Send edit token email if provided
    if (data.email) {
      try {
        await sendEditTokenEmail({
          to: data.email,
          caseNumber: null,
          editToken: rawToken,
          caseTitle: cleanTitle,
        });
        editLinkSent = true;
        logEvent({
          event: "submit.edit_link_sent",
          ipHash,
          postId: post.id,
        });
      } catch (emailErr) {
        // Non-fatal — post is saved, email just failed
        console.error("[posts] email error:", emailErr);
        logEvent({
          event: "submit.edit_link_failed",
          level: "warn",
          ipHash,
          postId: post.id,
        });
      }
    }

    logEvent({
      event: "submit.created",
      ipHash,
      postId: post.id,
      editLinkSent,
      tagCount: tags.length,
    });

    return NextResponse.json(
      {
        editLinkSent,
        message: "Submission received. It will appear after review.",
      },
      { status: 201 },
    );
  } catch (err) {
    console.error("[posts] unexpected error:", err);
    logEvent({
      event: "submit.unexpected_error",
      level: "error",
      error: err instanceof Error ? err.message : "unknown",
    });
    return NextResponse.json(
      { error: "Internal server error." },
      { status: 500 },
    );
  }
}
