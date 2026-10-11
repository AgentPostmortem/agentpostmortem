import type { Metadata } from "next";
import { notFound } from "next/navigation";
import Link from "next/link";
import { cache } from "react";
import { TagBadge } from "@/components/post/TagBadge";
import { VoteButtons } from "@/components/post/VoteButtons";
import { PostCard } from "@/components/post/PostCard";
import { fetchPostByCase, fetchRelatedPosts } from "@/lib/db/posts";
import { CopyLinkButton } from "@/components/post/CopyLinkButton";
import { CommentsSection } from "@/components/post/CommentsSection";
import { ArrowLeftIcon, ArrowRightIcon } from "@/components/ui/icons";
import { SeverityPill } from "@/components/post/SeverityPill";
import {
  SEVERITY_DESCRIPTIONS,
  SEVERITY_LABELS,
  severityStyle,
} from "@/lib/constants/severity";
import { getSiteUrl } from "@/lib/utils/urls";
import { incidentDate } from "@/lib/utils/incident-date";
import { formatUsd } from "@/lib/utils/format";

export const revalidate = 60;

// Cache the case fetch so generateMetadata and the page share the same result.
// Route-level ISR still uses the OpenNext incremental cache; this request-level
// memo avoids wrapping Supabase cookie reads in unstable_cache.
const getCachedCase = cache((caseNumber: string) =>
  fetchPostByCase(caseNumber),
);

function getSourceDomain(sourceUrl?: string) {
  if (!sourceUrl) return null;
  try {
    return new URL(sourceUrl).hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
}

interface PageProps {
  params: { caseNumber: string };
}

export async function generateMetadata({
  params,
}: PageProps): Promise<Metadata> {
  const post = await getCachedCase(params.caseNumber);
  const caseNum = params.caseNumber.toUpperCase();
  const title = post ? post.title : `Case ${caseNum}`;
  const description =
    post?.outcome?.slice(0, 160) ?? `AI agent failure case file ${caseNum}`;
  const siteUrl = getSiteUrl();
  const ogImageUrl = `${siteUrl}/api/og/${caseNum}`;

  return {
    title,
    description,
    alternates: {
      canonical: `/case/${caseNum}`,
    },
    openGraph: {
      title,
      description,
      url: `${siteUrl}/case/${caseNum}`,
      siteName: "AgentPostmortem",
      images: [{ url: ogImageUrl, width: 1200, height: 630 }],
      type: "article",
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: [ogImageUrl],
    },
  };
}

export default async function CasePage({ params }: PageProps) {
  const post = await getCachedCase(params.caseNumber);
  if (!post) notFound();

  const related = await fetchRelatedPosts(
    post.caseNumber,
    post.agentSlug,
    post.tags,
  );

  const s = severityStyle(post.damageLevel);
  const severityLabel = SEVERITY_LABELS[post.damageLevel as 1 | 2 | 3 | 4 | 5];

  const formattedDate = new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  }).format(new Date(incidentDate(post)));

  const cost = formatUsd(post.estimatedCostUsd);

  const siteUrl = getSiteUrl();
  const sourceDomain = getSourceDomain(post.sourceUrl);
  const articleJsonLd = {
    "@context": "https://schema.org",
    "@type": "Article",
    headline: post.title,
    description: post.outcome.slice(0, 160),
    datePublished: incidentDate(post),
    dateModified: post.createdAt,
    author: {
      "@type": "Organization",
      name: "AgentPostmortem",
    },
    publisher: {
      "@type": "Organization",
      name: "AgentPostmortem",
      url: siteUrl,
    },
    url: `${siteUrl}/case/${post.caseNumber}`,
    mainEntityOfPage: `${siteUrl}/case/${post.caseNumber}`,
    keywords: post.tags.join(", "),
    ...(post.sourceUrl
      ? {
          citation: {
            "@type": "CreativeWork",
            name: post.sourceTitle ?? sourceDomain ?? "Source report",
            url: post.sourceUrl,
          },
        }
      : {}),
  };
  const breadcrumbJsonLd = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      {
        "@type": "ListItem",
        position: 1,
        name: "Registry",
        item: siteUrl,
      },
      {
        "@type": "ListItem",
        position: 2,
        name: post.caseNumber,
        item: `${siteUrl}/case/${post.caseNumber}`,
      },
    ],
  };

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(articleJsonLd) }}
      />
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(breadcrumbJsonLd) }}
      />
      <div className="shell py-12 sm:py-16">
        <article className="mx-auto w-full max-w-[52rem]">
          {/* Breadcrumb */}
          <div className="mb-8 flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.14em] text-text-tertiary">
            <Link href="/" className="hover:text-text-secondary">
              Registry
            </Link>
            <span>/</span>
            <span>{post.caseNumber}</span>
          </div>

          {/* Case file header: the story leads, metadata follows */}
          <header className="mb-10">
            <div className="mb-5 flex flex-wrap items-center gap-2">
              <span className="stamp stamp-red">{post.caseNumber}</span>
              <SeverityPill level={post.damageLevel} />
              <span className="font-sans text-[13px] font-semibold text-text-tertiary">
                Reported {formattedDate}
              </span>
            </div>

            <h1 className="font-sans text-[2rem] font-bold leading-[1.05] tracking-[-0.03em] text-text-primary sm:text-[3rem]">
              {post.title}
            </h1>

            {post.tags.length > 0 && (
              <div className="mt-5 flex flex-wrap gap-1.5">
                {post.tags.map((tag) => (
                  <TagBadge key={tag} tag={tag} />
                ))}
              </div>
            )}
          </header>

          {/* Summary block: what happened, how bad, who, at a glance */}
          <section className="mb-10" aria-labelledby="summary-heading">
            <h2 id="summary-heading" className="section-label">
              At a Glance
            </h2>
            <dl className="grid grid-cols-1 gap-px bg-border-default sm:grid-cols-3">
              <div className="bg-bg-canvas px-4 py-3.5">
                <dt className="meta-key">Agent Involved</dt>
                <dd className="mt-1">
                  <Link
                    href={`/agent/${post.agentSlug}`}
                    className="meta-val transition-colors hover:text-accent"
                  >
                    {post.agentName}
                  </Link>
                </dd>
              </div>
              <div className="bg-bg-canvas px-4 py-3.5">
                <dt className="meta-key">Estimated Damage</dt>
                <dd className="meta-val mt-1">
                  {cost ? (
                    <span className="text-accent">~{cost}</span>
                  ) : (
                    <span className="text-text-tertiary">Not quantified</span>
                  )}
                </dd>
              </div>
              <div className={`px-4 py-3.5 ${s.bg}`}>
                <dt className="meta-key">Severity</dt>
                <dd className={`meta-val mt-1 ${s.text}`}>
                  {post.damageLevel} / 5 {severityLabel}
                </dd>
              </div>
              <div className="bg-bg-canvas px-4 py-3.5 sm:col-span-3">
                <dt className="meta-key">Impact Band</dt>
                <dd className="mt-1 text-sm leading-6 text-text-secondary">
                  {SEVERITY_DESCRIPTIONS[post.damageLevel]}
                </dd>
              </div>
            </dl>
            {/* Severity bar, redundant with the printed level above */}
            <div className="mt-px h-1 bg-bg-elevated" aria-hidden="true">
              <div className={`h-1 ${s.fill} ${s.bar}`} />
            </div>
          </section>

          {/* Source / accuracy disclaimer */}
          <p className="mb-10 font-mono text-[10px] leading-relaxed text-text-tertiary">
            Independent project · aggregated from public reports and may be
            unverified — see the primary source below · not affiliated with or
            endorsed by any company or product named.
          </p>

          {/* Instruction */}
          {post.prompt && (
            <section className="mb-10">
              <h2 className="section-label">Instruction Given to Agent</h2>
              <div className="border border-border-default">
                <div className="border-b border-border-default px-5 py-2.5">
                  <span className="font-mono text-[9px] uppercase tracking-widest text-text-tertiary">
                    Prompt
                  </span>
                </div>
                <blockquote className="px-5 py-5">
                  <p className="font-mono text-sm leading-[1.75] text-text-secondary">
                    &ldquo;{post.prompt}&rdquo;
                  </p>
                </blockquote>
              </div>
            </section>
          )}

          {/* Findings */}
          <section className="mb-10">
            <h2 className="section-label">What Happened</h2>
            <div className="border-t border-border-default pt-5">
              <p className="max-w-[68ch] text-[1.0625rem] leading-[1.75] text-text-primary">
                {post.outcome}
              </p>
            </div>
          </section>

          {(post.verifiedFacts.length > 0 ||
            post.unknowns.length > 0 ||
            post.lessons.length > 0) && (
            <section className="mb-10">
              <h2 className="section-label">Case Analysis</h2>
              <div className="divide-y divide-border-default border-y border-border-default">
                {post.verifiedFacts.length > 0 && (
                  <div className="py-6">
                    <h3 className="font-mono text-[10px] uppercase tracking-[0.18em] text-text-secondary">
                      Verified Facts
                    </h3>
                    <ul className="mt-4 space-y-2.5">
                      {post.verifiedFacts.map((fact) => (
                        <li
                          key={fact}
                          className="flex max-w-[68ch] gap-3 text-[0.95rem] leading-[1.7] text-text-primary"
                        >
                          <span className="mt-[13px] h-px w-2.5 shrink-0 bg-text-tertiary" />
                          <span>{fact}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {post.unknowns.length > 0 && (
                  <div className="py-6">
                    <h3 className="font-mono text-[10px] uppercase tracking-[0.18em] text-text-secondary">
                      Not Publicly Confirmed
                    </h3>
                    <ul className="mt-4 space-y-2.5">
                      {post.unknowns.map((unknown) => (
                        <li
                          key={unknown}
                          className="flex max-w-[68ch] gap-3 text-[0.95rem] leading-[1.7] text-text-secondary"
                        >
                          <span className="mt-[13px] h-px w-2.5 shrink-0 bg-border-strong" />
                          <span>{unknown}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {post.lessons.length > 0 && (
                  <div className="py-6">
                    <h3 className="font-mono text-[10px] uppercase tracking-[0.18em] text-text-secondary">
                      Operational Lessons
                    </h3>
                    <ul className="mt-4 space-y-2.5">
                      {post.lessons.map((lesson) => (
                        <li
                          key={lesson}
                          className="flex max-w-[68ch] gap-3 text-[0.95rem] leading-[1.7] text-text-primary"
                        >
                          <span className="mt-1 shrink-0 text-accent">
                            <ArrowRightIcon size={11} />
                          </span>
                          <span>{lesson}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </section>
          )}

          {post.sourceUrl && (
            <section className="mb-10">
              <h2 className="section-label">Primary Source</h2>
              <a
                href={post.sourceUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="block border border-border-default px-5 py-4 transition-colors hover:border-border-strong"
              >
                <span className="block text-sm leading-6 text-text-primary">
                  {post.sourceTitle ?? "Read the source report"}
                </span>
                <span className="mt-1 block font-sans text-[13px] font-semibold text-text-tertiary">
                  {sourceDomain} ↗
                </span>
              </a>
            </section>
          )}

          {/* Evidence */}
          {post.screenshots && post.screenshots.length > 0 && (
            <section className="mb-10">
              <h2 className="section-label">
                Evidence — {post.screenshots.length} Exhibit
                {post.screenshots.length > 1 ? "s" : ""}
              </h2>
              <div className="grid gap-3 sm:grid-cols-2">
                {post.screenshots.map((src, i) => (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    key={i}
                    src={src}
                    alt={`Exhibit ${i + 1}`}
                    className="border border-border-default"
                  />
                ))}
              </div>
            </section>
          )}

          {/* Case record: provenance and voting, deliberately after the story */}
          <section className="mb-10" aria-labelledby="record-heading">
            <h2 id="record-heading" className="section-label">
              Case Record
            </h2>
            <div className="flex flex-wrap items-center justify-between gap-4 border-y border-border-default py-5">
              <dl className="flex flex-wrap gap-x-8 gap-y-3">
                <div>
                  <dt className="meta-key">Case No.</dt>
                  <dd className="meta-val mt-0.5 tracking-[0.1em]">
                    {post.caseNumber}
                  </dd>
                </div>
                <div>
                  <dt className="meta-key">Reported</dt>
                  <dd className="meta-val mt-0.5">{formattedDate}</dd>
                </div>
                <div>
                  <dt className="meta-key">Attribution</dt>
                  <dd className="meta-val mt-0.5 text-text-secondary">
                    {post.isAnonymous
                      ? "Anonymous"
                      : post.authorHandle
                        ? `@${post.authorHandle}`
                        : "Practitioner"}
                  </dd>
                </div>
              </dl>
              <VoteButtons postId={post.id} initialScore={post.voteScore} />
            </div>
          </section>

          {/* Comments */}
          <CommentsSection postId={post.id} />

          {/* Related cases */}
          {related.length > 0 && (
            <div className="mt-14 border-t border-border-default pt-8">
              <h2 className="mb-5 font-sans text-[13px] font-semibold uppercase tracking-[0.12em] text-text-tertiary">
                More Cases
              </h2>
              <div className="space-y-2">
                {related.map((r) => (
                  <PostCard key={r.id} post={r} />
                ))}
              </div>
            </div>
          )}

          {/* Footer */}
          <div className="mt-14 flex flex-wrap items-center justify-between gap-4 border-t border-border-default pt-6">
            <div className="flex gap-5">
              <Link
                href="/"
                className="font-sans text-sm font-semibold text-text-tertiary hover:text-text-primary"
              >
                <span className="inline-flex items-center gap-1.5">
                  <ArrowLeftIcon size={10} /> All Cases
                </span>
              </Link>
              <Link
                href={`/agent/${post.agentSlug}`}
                className="font-sans text-sm font-semibold text-text-tertiary hover:text-text-primary"
              >
                <span className="inline-flex items-center gap-1.5">
                  More {post.agentName} <ArrowRightIcon size={10} />
                </span>
              </Link>
            </div>
            {/* Share */}
            <div className="flex items-center gap-3">
              <a
                href={`https://twitter.com/intent/tweet?text=${encodeURIComponent(`"${post.title}" — AI agent failure case ${post.caseNumber}`)}&url=${encodeURIComponent(`${getSiteUrl()}/case/${post.caseNumber}`)}`}
                target="_blank"
                rel="noopener noreferrer"
                className="font-sans text-sm font-semibold text-text-tertiary hover:text-text-primary"
              >
                <span className="inline-flex items-center gap-1.5">
                  Share on X <ArrowRightIcon size={10} />
                </span>
              </a>
              <CopyLinkButton caseNumber={post.caseNumber} />
            </div>
          </div>
        </article>
      </div>
    </>
  );
}
