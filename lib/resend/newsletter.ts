import { Resend } from "resend";
import { getSiteUrl } from "@/lib/utils/urls";

const resend = new Resend(process.env.RESEND_API_KEY || "re_dummy");
const FROM =
  process.env.RESEND_NEWSLETTER_FROM ??
  "AgentPostmortem <cases@agentpostmortem.com>";

function escapeHtml(s: string): string {
  return String(s).replace(
    /[&<>"']/g,
    (c) =>
      (
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        }) as Record<string, string>
      )[c],
  );
}

const SEVERITY: Record<number, { label: string; color: string }> = {
  1: { label: "MINIMAL", color: "#6b7280" },
  2: { label: "LOW", color: "#6b7280" },
  3: { label: "MODERATE", color: "#b45309" },
  4: { label: "SEVERE", color: "#b91c1c" },
  5: { label: "CRITICAL", color: "#991b1b" },
};

// Responsive email shell. Inline styles for clients that strip <style>;
// the <style> media query enhances small screens (desktop + mobile).
function shell(inner: string, unsubUrl: string): string {
  const site = getSiteUrl();
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<style>
  @media only screen and (max-width:620px){
    .apm-container{width:100% !important;border-radius:0 !important;border-left:0 !important;border-right:0 !important}
    .apm-pad{padding:22px !important}
    .apm-masthead{padding:18px 22px !important}
    .apm-h1{font-size:20px !important}
    .apm-btn{display:block !important;text-align:center !important}
  }
</style>
</head>
<body style="margin:0;padding:0;background:#f4f4f2;-webkit-text-size-adjust:100%">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0">Real AI agent failures, documented.</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f2">
    <tr><td align="center" style="padding:24px 12px">
      <table role="presentation" class="apm-container" width="600" cellpadding="0" cellspacing="0" style="width:600px;max-width:600px;background:#ffffff;border:1px solid #e7e7e3;border-radius:10px;overflow:hidden">
        <tr><td class="apm-masthead" style="background:#0e0e12;padding:20px 28px">
          <a href="${site}" style="text-decoration:none;color:#ffffff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;font-weight:700;font-size:16px">
            <img src="${site}/apple-icon" width="28" height="28" alt="" style="vertical-align:middle;border-radius:6px;margin-right:10px">AgentPostmortem
          </a>
          <div style="color:#8a8a93;font-size:11px;margin-top:6px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif">A registry of real AI agent failures.</div>
        </td></tr>
        <tr><td class="apm-pad" style="padding:28px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif">${inner}</td></tr>
        <tr><td style="border-top:1px solid #eee;padding:18px 28px;background:#fafafa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif">
          <p style="font-size:11px;line-height:1.6;color:#999;margin:0">Independent project — not affiliated with or endorsed by any company named. Cases are aggregated from public reports and may be unverified.</p>
          <p style="font-size:11px;color:#999;margin:8px 0 0"><a href="${unsubUrl}" style="color:#999">Unsubscribe</a> &middot; <a href="${site}" style="color:#999">agentpostmortem.com</a></p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>`;
}

export async function sendNewsletterWelcome(
  to: string,
  unsubToken: string,
): Promise<void> {
  const site = getSiteUrl();
  const unsub = `${site}/unsubscribe/${unsubToken}`;
  const inner = `
    <h1 class="apm-h1" style="font-family:Georgia,'Times New Roman',serif;font-weight:normal;font-size:22px;color:#111;margin:0 0 12px">You're on the list.</h1>
    <p style="font-size:14px;line-height:1.6;color:#444;margin:0 0 18px">
      Once a week you'll get a digest of newly documented AI agent failures — real
      incidents, what actually went wrong, and the operational lessons for teams
      shipping AI agents.
    </p>
    <a class="apm-btn" href="${site}" style="display:inline-block;background:#e5484d;color:#fff;text-decoration:none;font-size:13px;font-weight:600;padding:11px 20px;border-radius:6px">Browse the registry →</a>`;
  const { error } = await resend.emails.send({
    from: FROM,
    to,
    subject: "You're subscribed to AgentPostmortem",
    html: shell(inner, unsub),
  });
  if (error) throw new Error(error.message);
}

export interface DigestCase {
  caseNumber: string;
  title: string;
  outcome: string;
  damageLevel?: number;
  agentName?: string;
}

export async function sendNewsletterDigest(
  to: string,
  unsubToken: string,
  cases: DigestCase[],
): Promise<void> {
  const site = getSiteUrl();
  const unsub = `${site}/unsubscribe/${unsubToken}`;
  const cards = cases
    .map((c) => {
      const sev = SEVERITY[c.damageLevel ?? 0];
      const badge = sev
        ? `<span style="display:inline-block;font-size:9px;font-weight:700;letter-spacing:.5px;color:#fff;background:${sev.color};padding:3px 7px;border-radius:3px">${sev.label}</span>`
        : "";
      const meta = [c.caseNumber, c.agentName]
        .filter((x): x is string => Boolean(x))
        .map((x) => escapeHtml(x))
        .join(" &middot; ");
      return `
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-bottom:1px solid #f0f0f0">
        <tr><td style="padding:16px 0">
          <div style="margin-bottom:8px">${badge}</div>
          <a href="${site}/case/${escapeHtml(c.caseNumber)}" style="font-family:Georgia,serif;font-size:16px;line-height:1.35;color:#111;text-decoration:none">${escapeHtml(c.title)}</a>
          <div style="font-size:11px;color:#999;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;margin-top:4px">${meta}</div>
          <p style="font-size:13px;line-height:1.55;color:#555;margin:8px 0 0">${escapeHtml(c.outcome.slice(0, 200))}…</p>
          <a href="${site}/case/${escapeHtml(c.caseNumber)}" style="display:inline-block;margin-top:8px;font-size:12px;font-weight:600;color:#e5484d;text-decoration:none">Read the case →</a>
        </td></tr>
      </table>`;
    })
    .join("");
  const inner = `
    <h1 class="apm-h1" style="font-family:Georgia,serif;font-weight:normal;font-size:22px;color:#111;margin:0 0 6px">This week: ${cases.length} agent failure${cases.length === 1 ? "" : "s"}</h1>
    <p style="font-size:13px;color:#777;margin:0 0 4px">Newly documented incidents from the registry.</p>
    ${cards}
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding-top:24px">
      <a class="apm-btn" href="${site}" style="display:inline-block;background:#0e0e12;color:#fff;text-decoration:none;font-size:13px;font-weight:600;padding:11px 22px;border-radius:6px">See the full registry →</a>
    </td></tr></table>`;
  const { error } = await resend.emails.send({
    from: FROM,
    to,
    subject: `AgentPostmortem — this week's AI agent failures (${cases.length})`,
    html: shell(inner, unsub),
  });
  if (error) throw new Error(error.message);
}
