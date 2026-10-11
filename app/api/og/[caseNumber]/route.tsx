import { ImageResponse } from "next/og";
import { NextRequest } from "next/server";
import { SEVERITY_LABELS } from "@/lib/constants/severity";
import { formatUsd } from "@/lib/utils/format";

interface CaseData {
  caseNumber: string;
  title: string;
  agentName: string;
  company: string;
  damageLevel: number;
  estimatedCostUsd: number | null;
  outcome: string;
  tags: string[];
}

interface CaseRow {
  case_number: string;
  title: string;
  agents: { name: string; company: string | null } | null;
  damage_level: number;
  estimated_cost_usd: number | null;
  outcome: string | null;
  post_tags: { tags: { slug: string } | null }[];
}

async function fetchCase(caseNumber: string): Promise<CaseData | null> {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !key) return null;

  try {
    const res = await fetch(
      `${url}/rest/v1/posts?case_number=eq.${encodeURIComponent(caseNumber.toUpperCase())}&status=eq.approved&select=case_number,title,outcome,damage_level,estimated_cost_usd,agents(name,company),post_tags(tags(slug))&limit=1`,
      { headers: { apikey: key, Authorization: `Bearer ${key}` } },
    );
    if (!res.ok) return null;
    const rows = (await res.json()) as CaseRow[];
    if (!rows?.length) return null;
    const row = rows[0];
    return {
      caseNumber: row.case_number,
      title: row.title,
      agentName: row.agents?.name ?? "Unknown",
      company: row.agents?.company ?? "",
      damageLevel: row.damage_level,
      estimatedCostUsd: row.estimated_cost_usd ?? null,
      outcome: row.outcome ?? "",
      tags: (row.post_tags ?? [])
        .map((pt: { tags: { slug: string } | null }) => pt.tags?.slug)
        .filter((slug): slug is string => Boolean(slug)),
    };
  } catch {
    return null;
  }
}

export async function GET(
  _req: NextRequest,
  { params }: { params: { caseNumber: string } },
) {
  try {
    const data = await fetchCase(params.caseNumber);

    const title = data?.title ?? "AI Agent Failure";
    const caseNum = data?.caseNumber ?? params.caseNumber.toUpperCase();
    const agentName = data?.agentName ?? "Unknown";
    const company = data?.company ?? "";
    const outcome = data?.outcome ?? "";
    const damageLevel = data?.damageLevel ?? 3;
    const severityLabel =
      SEVERITY_LABELS[damageLevel as 1 | 2 | 3 | 4 | 5] ?? "Moderate";
    const costFormatted = formatUsd(data?.estimatedCostUsd);

    return new ImageResponse(
      <div
        style={{
          width: "1200px",
          height: "630px",
          backgroundColor: "#0c0b0a",
          display: "flex",
          flexDirection: "column",
          padding: "60px 80px",
          fontFamily: "sans-serif",
          position: "relative",
        }}
      >
        {/* Red top stripe */}
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            height: "4px",
            backgroundColor: "#f5a524",
          }}
        />

        {/* Top row */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            marginBottom: "48px",
          }}
        >
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            <span
              style={{
                fontSize: "12px",
                letterSpacing: "4px",
                textTransform: "uppercase",
                color: "#555",
                fontWeight: 600,
              }}
            >
              AgentPostmortem
            </span>
            <span
              style={{
                fontSize: "22px",
                letterSpacing: "3px",
                textTransform: "uppercase",
                color: "#f5a524",
                fontWeight: 700,
              }}
            >
              {caseNum}
            </span>
          </div>

          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "flex-end",
              gap: "4px",
              backgroundColor: "#111113",
              border: "1px solid #222",
              borderRadius: "6px",
              padding: "14px 22px",
            }}
          >
            <span
              style={{
                fontSize: "10px",
                letterSpacing: "3px",
                textTransform: "uppercase",
                color: "#555",
              }}
            >
              Subject Agent
            </span>
            <span
              style={{ fontSize: "22px", color: "#ededef", fontWeight: 600 }}
            >
              {agentName}
            </span>
            {company ? (
              <span style={{ fontSize: "13px", color: "#666" }}>{company}</span>
            ) : null}
          </div>
        </div>

        {/* Title */}
        <div
          style={{
            fontSize: "38px",
            lineHeight: 1.2,
            color: "#ededef",
            fontWeight: 600,
            marginBottom: "20px",
          }}
        >
          {title.length > 85 ? title.substring(0, 85) + "…" : title}
        </div>

        {/* Outcome */}
        {outcome ? (
          <div
            style={{
              fontSize: "16px",
              lineHeight: 1.6,
              color: "#777",
              flex: 1,
              display: "flex",
              alignItems: "flex-start",
            }}
          >
            {outcome.length > 140 ? outcome.substring(0, 140) + "…" : outcome}
          </div>
        ) : (
          <div style={{ flex: 1 }} />
        )}

        {/* Bottom row */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-end",
            borderTop: "1px solid #1e1e22",
            paddingTop: "24px",
          }}
        >
          {/* Severity */}
          <div
            style={{ display: "flex", flexDirection: "column", gap: "10px" }}
          >
            <span
              style={{
                fontSize: "10px",
                letterSpacing: "3px",
                textTransform: "uppercase",
                color: "#555",
                fontWeight: 600,
              }}
            >
              Severity — {severityLabel}
            </span>
            <div style={{ display: "flex", gap: "6px" }}>
              {[1, 2, 3, 4, 5].map((pip) => (
                <div
                  key={pip}
                  style={{
                    width: "44px",
                    height: "8px",
                    borderRadius: "3px",
                    backgroundColor: pip <= damageLevel ? "#f5a524" : "#1c1917",
                    border: `1px solid ${pip <= damageLevel ? "#f5a524" : "#33302b"}`,
                  }}
                />
              ))}
            </div>
          </div>

          {/* Cost */}
          {costFormatted ? (
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "flex-end",
                gap: "4px",
              }}
            >
              <span
                style={{
                  fontSize: "10px",
                  letterSpacing: "3px",
                  textTransform: "uppercase",
                  color: "#555",
                  fontWeight: 600,
                }}
              >
                Est. Damage
              </span>
              <span
                style={{ fontSize: "36px", color: "#f5a524", fontWeight: 700 }}
              >
                {costFormatted}
              </span>
            </div>
          ) : null}

          {/* Domain */}
          <span
            style={{
              fontSize: "12px",
              letterSpacing: "2px",
              textTransform: "uppercase",
              color: "#333",
            }}
          >
            agentpostmortem.com
          </span>
        </div>
      </div>,
      { width: 1200, height: 630 },
    );
  } catch (err) {
    // Return a plain error image so we can see what's failing
    return new ImageResponse(
      <div
        style={{
          width: "1200px",
          height: "630px",
          backgroundColor: "#1a0000",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "#ff4444",
          fontSize: "24px",
        }}
      >
        Error: {String(err)}
      </div>,
      { width: 1200, height: 630 },
    );
  }
}
