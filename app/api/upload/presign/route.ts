import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { getPresignedUploadUrl } from "@/lib/r2/upload";
import { hashIp, getClientIp } from "@/lib/utils/hash";
import { logEvent } from "@/lib/observability/events";
import { consumeSharedRateLimit } from "@/lib/rate-limit/shared";

const ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif"];
const MAX_SIZE_BYTES = 5 * 1024 * 1024; // 5 MB

const schema = z.object({
  filename: z.string().min(1).max(255),
  contentType: z.string().refine((t) => ALLOWED_TYPES.includes(t), {
    message: "Only JPEG, PNG, WEBP, and GIF images are allowed.",
  }),
  size: z
    .number()
    .int("File size must be an integer byte count.")
    .min(1, "File size must be greater than 0 bytes.")
    .max(MAX_SIZE_BYTES, "File must be under 5 MB."),
});

const WINDOW_SECONDS = 10 * 60;
const MAX_REQUESTS = 10;

export async function POST(req: NextRequest) {
  try {
    const ip = getClientIp(req.headers);
    const ipHash = hashIp(ip);

    const rateLimit = await consumeSharedRateLimit(
      `upload:${ipHash}`,
      WINDOW_SECONDS,
      MAX_REQUESTS,
    );

    if (!rateLimit.allowed) {
      logEvent({
        event: "upload_presign.rate_limited",
        level: "warn",
        ipHash,
      });
      return NextResponse.json(
        { error: "Too many upload requests. Try again later." },
        { status: 429 },
      );
    }

    const body: unknown = await req.json();
    const parsed = schema.safeParse(body);
    if (!parsed.success) {
      return NextResponse.json(
        { error: parsed.error.issues[0].message },
        { status: 400 },
      );
    }

    const { filename, contentType, size } = parsed.data;

    const result = await getPresignedUploadUrl(
      filename,
      contentType,
      "screenshots",
      size,
    );

    return NextResponse.json({
      url: result.uploadUrl,
      publicUrl: result.publicUrl,
      key: result.key,
    });
  } catch (err) {
    console.error("[presign] error:", err);
    logEvent({
      event: "upload_presign.failed",
      level: "error",
      error: err instanceof Error ? err.message : "unknown",
    });
    return NextResponse.json(
      { error: "Failed to generate upload URL." },
      { status: 500 },
    );
  }
}
