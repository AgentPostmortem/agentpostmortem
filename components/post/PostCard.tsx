import Link from "next/link";
import { VoteButtons } from "./VoteButtons";
import { TagBadge } from "./TagBadge";
import { SeverityPill } from "./SeverityPill";
import { severityStyle } from "@/lib/constants/severity";
import { incidentDate } from "@/lib/utils/incident-date";
import type { Post } from "@/types";

import { formatUsd } from "@/lib/utils/format";

type PostCardProps = { post: Post; commentCount?: number };

export function PostCard({ post, commentCount }: PostCardProps) {
  const s = severityStyle(post.damageLevel);

  const date = new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(incidentDate(post)));

  const cost = formatUsd(post.estimatedCostUsd);

  return (
    <article className="group relative flex overflow-hidden rounded-sm border border-border-default bg-bg-surface transition-colors hover:border-border-strong hover:bg-bg-elevated">
      {/* Severity stripe: width is constant, height of fill encodes level */}
      <div
        aria-hidden="true"
        className="relative w-[3px] shrink-0 bg-border-default"
      >
        <div
          className={`absolute inset-x-0 bottom-0 ${s.stripe}`}
          style={{ height: `${post.damageLevel * 20}%` }}
        />
      </div>

      {/* Vote column */}
      <div className="flex w-10 shrink-0 flex-col items-center justify-start border-r border-border-default pb-3 pt-3">
        <VoteButtons postId={post.id} initialScore={post.voteScore} compact />
      </div>

      {/* Body */}
      <div className="min-w-0 flex-1 px-3.5 py-3 sm:px-4">
        {/* Registry metadata row */}
        <div className="mb-2 flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-x-2 overflow-hidden">
            <span className="shrink-0 font-mono text-[10px] font-medium tabular-nums tracking-[0.12em] text-accent">
              {post.caseNumber}
            </span>
            <span aria-hidden="true" className="shrink-0 text-border-strong">
              /
            </span>
            <Link
              href={`/agent/${post.agentSlug}`}
              className="truncate font-sans text-[13px] font-semibold text-text-secondary transition-colors hover:text-text-primary"
            >
              {post.agentName}
            </Link>
            <SeverityPill level={post.damageLevel} className="shrink-0" />
            {cost && (
              <span className="shrink-0 font-mono text-[10px] font-medium tabular-nums text-text-secondary">
                ~{cost}
              </span>
            )}
          </div>

          <span className="shrink-0 font-mono text-[10px] tabular-nums text-text-tertiary">
            {date}
          </span>
        </div>

        {/* Title */}
        <Link href={`/case/${post.caseNumber.toLowerCase()}`}>
          <h2 className="font-sans text-[1.02rem] font-semibold leading-snug tracking-tight text-text-primary transition-colors group-hover:text-accent">
            {post.title}
          </h2>
        </Link>

        {/* Outcome */}
        <p className="mt-1.5 line-clamp-2 text-sm leading-relaxed text-text-secondary">
          {post.outcome}
        </p>

        {/* Tags + counts */}
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
          {post.tags.slice(0, 4).map((tag) => (
            <TagBadge key={tag} tag={tag} />
          ))}
          {post.tags.length > 4 && (
            <span className="font-mono text-[10px] tabular-nums text-text-tertiary">
              +{post.tags.length - 4} more
            </span>
          )}
          <span className="ml-auto flex items-center gap-3">
            {!post.isAnonymous && post.authorHandle && (
              <span className="font-sans text-xs text-text-tertiary">
                via @{post.authorHandle}
              </span>
            )}
            {commentCount != null && commentCount > 0 && (
              <span className="font-mono text-[10px] tabular-nums text-text-tertiary">
                {commentCount} comment{commentCount !== 1 ? "s" : ""}
              </span>
            )}
          </span>
        </div>
      </div>
    </article>
  );
}
