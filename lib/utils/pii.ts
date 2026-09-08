/**
 * PII redaction utilities.
 *
 * Applied to all user-submitted text before insertion into the database.
 * Redacts email addresses and phone numbers with placeholder tokens.
 *
 * These patterns favor common phone and email shapes and avoid redacting
 * structured identifiers such as dates and version numbers.
 */

/** Matches common email formats */
const EMAIL_PATTERN = /[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}/g;

/**
 * Matches phone numbers with a recognizable phone structure:
 * +1-555-555-5555, (555) 555-1212, 555.555.5555, +44 7700 900123, etc.
 *
 * Requiring a separator between phone-sized groups avoids treating arbitrary
 * digit runs, dates, versions, and identifiers as phone numbers.
 */
const PHONE_PATTERN =
  /(?<![\w])(?:\+\d{7,15}|(?:\+\d{1,3}[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?|\d{3,5}[\s.-])(?:\d{3}[\s.-]\d{4}|\d{3,4}[\s.-]\d{4}|\d{4}|\d{6}))(?:\s?(?:#|x\.?|ext\.?|extension)\s?\d+)?(?![\w])/g;

/** Minimum digit count for something to qualify as a phone number */
const MIN_PHONE_DIGITS = 7;

/**
 * Redact email addresses from text.
 */
export function redactEmails(text: string): string {
  return text.replace(EMAIL_PATTERN, "[EMAIL REDACTED]");
}

/**
 * Redact phone numbers from text.
 * Only replaces matches that contain at least MIN_PHONE_DIGITS digits.
 */
export function redactPhones(text: string): string {
  return text.replace(PHONE_PATTERN, (match) => {
    const digits = match.replace(/\D/g, "");
    if (digits.length < MIN_PHONE_DIGITS) return match;
    if (/^0+$/.test(digits)) return match;
    return "[PHONE REDACTED]";
  });
}

/**
 * Run all PII redaction passes on a string.
 * Apply to every user-submitted text field before storage.
 */
export function redactPii(text: string): string {
  let result = text;
  result = redactEmails(result);
  result = redactPhones(result);
  return result;
}
