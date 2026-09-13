import { z } from "zod";
import { isOwnedScreenshotUrl } from "@/lib/utils/urls";

export const MAX_AUTHOR_HANDLE_LENGTH = 64;

export const submitSchema = z.object({
  /** Slug of the agent involved (from AGENTS constant) */
  agentSlug: z.string().min(1, "Please select the AI agent involved.").max(64),

  /** Public case title */
  title: z
    .string()
    .min(20, "Title must be at least 20 characters.")
    .max(200, "Title must be 200 characters or fewer."),

  /** Optional: the exact prompt or instruction given to the agent */
  prompt: z
    .string()
    .max(2000, "Prompt must be 2000 characters or fewer.")
    .optional(),

  /** Full description of what happened and consequences */
  outcome: z
    .string()
    .min(100, "Please describe what happened in at least 100 characters.")
    .max(10000, "Outcome description must be 10,000 characters or fewer."),

  /** Severity 1–5 */
  damageLevel: z.number().int().min(1).max(5) as z.ZodType<1 | 2 | 3 | 4 | 5>,

  /** Approximate USD financial damage — 0 for reputational only */
  estimatedCostUsd: z.number().int().min(0).max(100_000_000).optional(),

  /** Tags — at least one required */
  tags: z
    .array(z.string().max(64))
    .min(1, "Please select at least one tag.")
    .max(8, "Maximum 8 tags per submission."),

  /** Whether the submitter is anonymous */
  isAnonymous: z.boolean().default(true),

  /** Optional display handle or company name (when not anonymous) */
  authorHandle: z.string().max(MAX_AUTHOR_HANDLE_LENGTH).optional(),

  /** Optional email to receive the edit token — never stored long-term */
  email: z
    .string()
    .email("Please enter a valid email address.")
    .optional()
    .or(z.literal("")),

  /** Public URLs for screenshot evidence — must point at our own R2 bucket */
  screenshots: z
    .array(
      z
        .string()
        .url()
        .refine(isOwnedScreenshotUrl, "Screenshot URL is not allowed."),
    )
    .max(5, "Maximum 5 screenshots per submission.")
    .optional(),
});

export type SubmitFormValues = z.infer<typeof submitSchema>;

/** Shared validator for the `screenshotUrls` field the submit/edit API routes accept. */
export const screenshotUrlsSchema = z
  .array(
    z
      .string()
      .url()
      .refine(isOwnedScreenshotUrl, "Screenshot URL is not allowed."),
  )
  .max(5)
  .optional();
