import { NextRequest, NextResponse } from "next/server";
import { createSupabaseServerClient } from "@/lib/supabase/server";
import { createSupabaseAdminClient } from "@/lib/supabase/admin";
import { consumeSharedRateLimit } from "@/lib/rate-limit/shared";
import { hashIp, getClientIp } from "@/lib/utils/hash";
import { MAX_AUTHOR_HANDLE_LENGTH } from "@/lib/schemas/submit";

interface CommentBody {
  post_id?: unknown;
  body?: unknown;
  is_anonymous?: unknown;
  author_handle?: unknown;
}

export async function GET(req: NextRequest) {
  const postId = req.nextUrl.searchParams.get("post_id");
  if (!postId) return NextResponse.json({ comments: [] });

  const supabase = createSupabaseServerClient();
  const { data, error } = await supabase
    .from("comments")
    .select("id, body, is_anonymous, author_handle, created_at")
    .eq("post_id", postId)
    .eq("status", "visible")
    .order("created_at", { ascending: true });

  if (error) return NextResponse.json({ comments: [] });
  return NextResponse.json({ comments: data ?? [] });
}

export async function POST(req: NextRequest) {
  try {
    const body = (await req.json()) as CommentBody;
    const { post_id, body: text, is_anonymous, author_handle } = body;

    if (typeof post_id !== "string" || typeof text !== "string") {
      return NextResponse.json({ error: "Missing fields" }, { status: 400 });
    }

    const trimmed = text.trim();
    if (trimmed.length < 3 || trimmed.length > 2000) {
      return NextResponse.json(
        { error: "Comment must be 3–2000 characters" },
        { status: 400 },
      );
    }

    const isAnon = typeof is_anonymous === "boolean" ? is_anonymous : true;
    const handle =
      typeof author_handle === "string" ? author_handle.trim() : null;
    if (handle && handle.length > MAX_AUTHOR_HANDLE_LENGTH) {
      return NextResponse.json(
        {
          error: `Handle must be ${MAX_AUTHOR_HANDLE_LENGTH} characters or fewer.`,
        },
        { status: 400 },
      );
    }
    if (!isAnon && !handle) {
      return NextResponse.json(
        { error: "A handle is required when posting non-anonymously." },
        { status: 400 },
      );
    }

    const ip = getClientIp(req.headers);
    const ip_hash = hashIp(ip);

    const rateLimit = await consumeSharedRateLimit(
      `comment:${ip_hash}`,
      3600,
      5,
    );
    if (!rateLimit.allowed) {
      return NextResponse.json(
        { error: "Too many comments. Try again later." },
        { status: 429 },
      );
    }

    const supabase = createSupabaseAdminClient();

    const { data, error } = await supabase
      .from("comments")
      .insert({
        post_id,
        body: trimmed,
        is_anonymous: isAnon,
        author_handle: isAnon ? null : handle,
        ip_hash,
        status: "visible",
      })
      .select("id, body, is_anonymous, author_handle, created_at")
      .single();

    if (error) throw error;
    return NextResponse.json({ comment: data }, { status: 201 });
  } catch (err) {
    console.error("[comments] error:", err);
    return NextResponse.json(
      { error: "Failed to post comment" },
      { status: 500 },
    );
  }
}
