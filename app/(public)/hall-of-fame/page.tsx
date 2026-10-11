import type { Metadata } from "next";
import Link from "next/link";
import { fetchFeedPosts } from "@/lib/db/posts";
import { VoteButtons } from "@/components/post/VoteButtons";
import { Pagination } from "@/components/ui/Pagination";
import { EmptyState } from "@/components/ui/EmptyState";
import { formatUsd } from "@/lib/utils/format";

export const revalidate = 60;

export const metadata: Metadata = {
  title: "Hall of Fame — Most Catastrophic AI Agent Failures",
  description:
    "The top 100 most catastrophic AI agent failures, ranked by community vote.",
};

const SEVERITY_COLOR: Record<number, string> = {
  1: "text-text-tertiary",
  2: "text-text-tertiary",
  3: "text-text-secondary",
  4: "text-accent/80",
  5: "text-accent",
};

const SEVERITY_LABEL: Record<number, string> = {
  1: "MINIMAL",
  2: "LOW",
  3: "MODERATE",
  4: "SEVERE",
  5: "CRITICAL",
};

interface PageProps {
  searchParams: { page?: string };
}

export default async function HallOfFamePage({ searchParams }: PageProps) {
  const currentPage = Math.max(1, parseInt(searchParams.page ?? "1") || 1);
  const { posts, total } = await fetchFeedPosts("hof", currentPage);
  const totalPages = Math.ceil(total / 20);
  const rankOffset = (currentPage - 1) * 20;

  function pageHref(p: number) {
    return p === 1 ? "/hall-of-fame" : `/hall-of-fame?page=${p}`;
  }

  return (
    <div className="shell py-12 sm:py-16">
      <div className="mb-8">
        <div className="mb-3 flex items-center gap-2">
          <span className="stamp stamp-red">Hall of Fame</span>
        </div>
        <h1 className="font-sans text-4xl font-bold tracking-tight text-text-primary">
          Most Catastrophic Failures
        </h1>
        <p className="mt-3 text-sm text-text-secondary">
          The top cases ranked by community vote. These are the incidents that
          define AI agent risk.
        </p>
      </div>

      {posts.length === 0 ? (
        <EmptyState
          title="No cases on file yet."
          action={{ label: "File the first report", href: "/submit" }}
        />
      ) : (
        <>
          <div className="overflow-x-auto rounded-sm border border-border-default bg-bg-surface">
            {/* Header row */}
            <div className="flex items-center gap-4 border-b border-border-default bg-bg-elevated px-4 py-2">
              <span className="w-8 font-mono text-[9px] uppercase tracking-widest text-text-tertiary">
                #
              </span>
              <span className="flex-1 font-mono text-[9px] uppercase tracking-widest text-text-tertiary">
                Case
              </span>
              <span className="hidden w-24 text-right font-mono text-[9px] uppercase tracking-widest text-text-tertiary sm:inline">
                Severity
              </span>
              <span className="w-28 text-right font-mono text-[9px] uppercase tracking-widest text-text-tertiary">
                Score
              </span>
            </div>

            <ol>
              {posts.map((post, i) => {
                const rank = rankOffset + i;
                const isTop3 = rank < 3;
                const cost = formatUsd(post.estimatedCostUsd);

                return (
                  <li
                    key={post.id}
                    className={[
                      "flex items-center gap-4 border-b border-border-default px-4 py-3 transition-colors hover:bg-bg-elevated",
                      isTop3 && "bg-bg-elevated/50",
                    ]
                      .filter(Boolean)
                      .join(" ")}
                  >
                    {/* Rank */}
                    <div className="w-8 shrink-0">
                      <span
                        className={[
                          "font-mono text-sm font-bold tabular-nums",
                          rank === 0
                            ? "text-accent"
                            : rank < 3
                              ? "text-text-secondary"
                              : "text-text-tertiary",
                        ].join(" ")}
                      >
                        {String(rank + 1).padStart(2, "0")}
                      </span>
                    </div>

                    {/* Content */}
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                        <span className="font-sans text-xs text-text-tertiary">
                          {post.caseNumber}
                        </span>
                        <span className="text-border-strong">·</span>
                        <Link
                          href={`/agent/${post.agentSlug}`}
                          className="font-mono text-[10px] text-text-secondary hover:text-text-primary"
                        >
                          {post.agentName}
                        </Link>
                        {cost && (
                          <>
                            <span className="text-border-strong">·</span>
                            <span className="font-mono text-[10px] font-semibold text-accent">
                              ~{cost}
                            </span>
                          </>
                        )}
                      </div>
                      <Link href={`/case/${post.caseNumber.toLowerCase()}`}>
                        <p
                          className={[
                            "mt-0.5 font-sans font-semibold leading-snug text-text-primary transition-colors hover:text-accent",
                            isTop3 ? "text-base" : "text-sm",
                          ].join(" ")}
                        >
                          {post.title}
                        </p>
                      </Link>
                    </div>

                    {/* Severity */}
                    <div
                      className={`hidden w-24 text-right font-mono text-[10px] sm:block ${SEVERITY_COLOR[post.damageLevel]}`}
                    >
                      {SEVERITY_LABEL[post.damageLevel]}
                    </div>

                    {/* Vote buttons */}
                    <div className="flex w-28 justify-end">
                      <VoteButtons
                        postId={post.id}
                        initialScore={post.voteScore}
                      />
                    </div>
                  </li>
                );
              })}
            </ol>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <Pagination
              currentPage={currentPage}
              totalPages={totalPages}
              hrefForPage={pageHref}
            />
          )}
        </>
      )}
    </div>
  );
}
