import { Resend } from "resend";
import { EditTokenEmail } from "./templates/EditTokenEmail";
import { getSiteUrl } from "@/lib/utils/urls";

const resend = new Resend(process.env.RESEND_API_KEY || "re_dummy");

interface SendEditTokenEmailParams {
  to: string;
  caseNumber: string | null;
  editToken: string;
  caseTitle: string;
}

/**
 * Send a one-time edit token email to the case submitter.
 * The token allows them to edit or retract their submission.
 */
export async function sendEditTokenEmail({
  to,
  caseNumber,
  editToken,
  caseTitle,
}: SendEditTokenEmailParams): Promise<void> {
  const appUrl = getSiteUrl();
  const editUrl = `${appUrl}/edit/${editToken}`;
  const statusUrl = `${appUrl}/status/${editToken}`;
  const subjectRef = caseNumber ?? "Pending submission";

  const { error } = await resend.emails.send({
    from: process.env.RESEND_FROM_EMAIL ?? "noreply@agentpostmortem.com",
    to,
    subject: `Your edit link for ${subjectRef}`,
    react: EditTokenEmail({
      caseNumber,
      caseTitle,
      editUrl,
      statusUrl,
    }),
  });

  if (error) {
    console.error("[resend] Failed to send edit token email:", error);
    throw new Error(`Failed to send email: ${error.message}`);
  }
}

interface SendApprovalEmailParams {
  to: string;
  caseNumber: string;
  caseTitle: string;
  caseUrl: string;
}

export async function sendApprovalEmail({
  to,
  caseNumber,
  caseTitle,
  caseUrl,
}: SendApprovalEmailParams): Promise<void> {
  const { error } = await resend.emails.send({
    from: process.env.RESEND_FROM_EMAIL ?? "noreply@agentpostmortem.com",
    to,
    subject: `Your case ${caseNumber} is now live on AgentPostmortem`,
    html: `
<!DOCTYPE html>
<html>
<body style="background:#0c0b0a;font-family:Georgia,serif;margin:0;padding:40px 16px;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="background:#111113;border:1px solid #232326;border-radius:4px;overflow:hidden;">
        <tr><td style="background:#f5a524;height:3px;font-size:0;">&nbsp;</td></tr>
        <tr><td style="padding:32px 40px 24px;">
          <p style="font-family:monospace;font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:#5a5a5a;margin:0 0 8px;">AgentPostmortem</p>
          <p style="font-family:monospace;font-size:16px;letter-spacing:.15em;text-transform:uppercase;color:#f5a524;margin:0;font-weight:bold;">${caseNumber}</p>
        </td></tr>
        <tr><td style="border-top:1px solid #232326;padding:0;font-size:0;">&nbsp;</td></tr>
        <tr><td style="padding:32px 40px;">
          <p style="color:#a0a0a6;font-size:13px;font-family:monospace;letter-spacing:.05em;text-transform:uppercase;margin:0 0 8px;">Case Approved</p>
          <p style="color:#ededef;font-size:18px;line-height:1.4;margin:0 0 24px;">${caseTitle}</p>
          <p style="color:#a0a0a6;font-size:14px;line-height:1.6;margin:0 0 28px;">Your submission has been reviewed and approved. It is now publicly visible in the AgentPostmortem registry.</p>
          <a href="${caseUrl}" style="display:inline-block;background:#1c1206;border:1px solid #f5a524;border-radius:3px;color:#f5a524;font-family:monospace;font-size:11px;font-weight:bold;letter-spacing:.15em;text-decoration:none;text-transform:uppercase;padding:12px 24px;">View Case →</a>
        </td></tr>
        <tr><td style="border-top:1px solid #232326;padding:16px 40px;">
          <p style="color:#37373c;font-size:10px;font-family:monospace;letter-spacing:.1em;margin:0;">AgentPostmortem — agentpostmortem.com</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>`,
  });

  if (error) {
    console.error("[resend] Failed to send approval email:", error);
  }
}
