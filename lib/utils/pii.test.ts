import { describe, expect, it } from "vitest";
import { redactEmails, redactPhones, redactPii } from "./pii";

describe("redactPii", () => {
  it("redacts email addresses and phone numbers", () => {
    const input =
      "Reach me at person@example.com or +1 (415) 555-1212 before launch.";

    expect(redactPii(input)).toBe(
      "Reach me at [EMAIL REDACTED] or [PHONE REDACTED] before launch.",
    );
  });

  it("redacts every email in one string", () => {
    expect(
      redactEmails("Contact alice@example.com and bob@domain.org today"),
    ).toBe("Contact [EMAIL REDACTED] and [EMAIL REDACTED] today");
  });

  it("redacts international phone numbers", () => {
    expect(redactPhones("Call me at +44 7700 900123")).toBe(
      "Call me at [PHONE REDACTED]",
    );
  });
  it("redacts domestic phone numbers with a leading zero", () => {
    expect(redactPhones("Call me at 07700 900123")).toBe(
      "Call me at [PHONE REDACTED]",
    );
  });

  it("leaves a short extension-only digit run alone", () => {
    expect(redactPhones("ext 12")).toBe("ext 12");
  });

  it("documents that an extension attached to a phone is redacted together", () => {
    // Known over-match: PHONE_PATTERN includes the `ext 12` suffix, so the
    // whole match is redacted rather than only the main number.
    expect(redactPhones("call 555-1212 ext 12")).toBe("call [PHONE REDACTED]");
  });

  it("leaves a long non-phone digit run alone", () => {
    expect(redactPhones("Order 12345678901234567890")).toBe(
      "Order 12345678901234567890",
    );
  });

  it("passes text without PII through unchanged", () => {
    expect(redactPii("This sentence has no PII.")).toBe(
      "This sentence has no PII.",
    );
  });

  it("documents with less than 7 digit number notredacted", () => {
    // as  the Minimum digit count for something to qualify as a phone number is 7
    expect(redactPhones("Order 123456")).toBe("Order 123456");
  });

  it("doesnt redacts a long tracking number as a phone number", () => {
    expect(redactPhones("Tracking number: 9400111899223856928490")).toBe(
      "Tracking number: 9400111899223856928490",
    );
  });

  // non-phone structured values

  it("over-redacts emails with invalid local-part separators", () => {
    expect(
      redactEmails(
        "Contact alice.@example.com and alice..smith@example.com today",
      ),
    ).toBe("Contact [EMAIL REDACTED] and [EMAIL REDACTED] today");
  });

  it("over-redacts emails with invalid domain separators", () => {
    expect(
      redactEmails("Contact bob@-domain.org and bob@domain-.org today"),
    ).toBe("Contact [EMAIL REDACTED] and [EMAIL REDACTED] today");
  });

  it("over-redacts emails with leading dots in the domain", () => {
    expect(redactEmails("Contact bob@.domain.org today")).toBe(
      "Contact [EMAIL REDACTED] today",
    );
  });

  it("over-redacts emails with consecutive dots in the domain", () => {
    expect(redactEmails("Contact bob@domain..org today")).toBe(
      "Contact [EMAIL REDACTED] today",
    );
  });

  it("over-redacts an email inside a URL", () => {
    expect(redactEmails("Visit https://alice@example.com/profile today")).toBe(
      "Visit https://[EMAIL REDACTED]/profile today",
    );
  });

  it("over-redacts a documentation email", () => {
    expect(redactEmails("Use user@example.com in the documentation")).toBe(
      "Use [EMAIL REDACTED] in the documentation",
    );
  });

  it("over-redacts multiple invalid email-like values in one string", () => {
    expect(
      redactEmails(
        "Values: _alice@example.com bob@domain..org carol.@example.com",
      ),
    ).toBe("Values: [EMAIL REDACTED] [EMAIL REDACTED] [EMAIL REDACTED]");
  });

  it("leaves a plain number alone", () => {
    expect(redactPhones("Reference number: 1234567")).toBe(
      "Reference number: 1234567",
    );
  });

  it("leaves a long numeric identifier alone", () => {
    expect(redactPhones("Order ID: 001234567890")).toBe(
      "Order ID: 001234567890",
    );
  });

  it("leaves a version number alone", () => {
    expect(redactPhones("Build version: 1.234.567")).toBe(
      "Build version: 1.234.567",
    );
  });

  it("leaves an IP address alone", () => {
    expect(redactPhones("Server IP: 192.168.1.1")).toBe(
      "Server IP: 192.168.1.1",
    );
  });

  it("leaves an ISO date and timestamp alone", () => {
    const input = "Created at: 2026-01-14T13:50:34.240Z";

    expect(redactPhones(input)).toBe(input);
  });

  it("leaves a compact timestamp alone", () => {
    expect(redactPhones("Created at: 20260806144500")).toBe(
      "Created at: 20260806144500",
    );
  });

  it("leaves a postal code alone", () => {
    expect(redactPhones("PIN code: 11000123")).toBe("PIN code: 11000123");
  });

  it("leaves a currency amount alone", () => {
    expect(redactPhones("Total amount: 123.456.789")).toBe(
      "Total amount: 123.456.789",
    );
  });

  it("leaves coordinates alone", () => {
    expect(redactPhones("Location: 28.6139 77.2090")).toBe(
      "Location: 28.6139 77.2090",
    );
  });

  it("leaves an all-zero placeholder alone", () => {
    expect(redactPhones("Placeholder: 000-000-0000")).toBe(
      "Placeholder: 000-000-0000",
    );
  });

  it("leaves a parenthesized value alone", () => {
    expect(redactPhones("Value: (2026) 1234567")).toBe(
      "Value: (2026) 1234567",
    );
  });

  it("leaves an extension-like value alone", () => {
    expect(redactPhones("Extension: ext-1234568")).toBe(
      "Extension: ext-1234568",
    );
  });

  it("leaves a number embedded in an identifier alone", () => {
    expect(redactPhones("Serial number: ABC-1234567-XYZ")).toBe(
      "Serial number: ABC-1234567-XYZ",
    );
  });

  it("leaves multiple non-phone values alone", () => {
    const input =
      "Date 2026-08-06, IP 192.168.1.1, version 1.234.567, order 001234567890";

    expect(redactPhones(input)).toBe(input);
  });

  it("leaves CVE identifiers, token counts, and commit ranges alone", () => {
    const input =
      "CVE-2024-12345 used 8 240 000 000 tokens; commits abc1234..def5678";

    expect(redactPhones(input)).toBe(input);
  });

  it("over-redacts an email embedded in a larger token", () => {
    expect(redactEmails("Contact prefixalice@example.comsuffix today")).toBe(
      "Contact [EMAIL REDACTED] today",
    );
  });
});
