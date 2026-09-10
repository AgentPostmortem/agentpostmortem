import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { s3Client, putObjectCommand, getObjectCommand, getSignedUrl } =
  vi.hoisted(() => ({
    s3Client: vi.fn(() => ({})),
    putObjectCommand: vi.fn((input: unknown) => ({ input })),
    getObjectCommand: vi.fn((input: unknown) => ({ input })),
    getSignedUrl: vi.fn(async () => "https://upload.example/signed"),
  }));

vi.mock("@aws-sdk/client-s3", () => ({
  S3Client: s3Client,
  PutObjectCommand: putObjectCommand,
  GetObjectCommand: getObjectCommand,
}));

vi.mock("@aws-sdk/s3-request-presigner", () => ({
  getSignedUrl,
}));

describe("getPresignedUploadUrl", () => {
  const originalEnv = { ...process.env };
  let upload: typeof import("./upload");

  beforeEach(async () => {
    vi.resetModules();
    process.env.R2_ACCOUNT_ID = "test-account";
    process.env.R2_ACCESS_KEY_ID = "test-key";
    process.env.R2_SECRET_ACCESS_KEY = "test-secret";
    process.env.R2_BUCKET_NAME = "test-bucket";
    process.env.R2_PUBLIC_URL = "https://cdn.example.com";
    s3Client.mockClear();
    putObjectCommand.mockClear();
    getObjectCommand.mockClear();
    getSignedUrl.mockClear();
    upload = await import("./upload");
  });

  afterEach(() => {
    process.env = { ...originalEnv };
  });

  it.each([
    ["image/jpeg", "jpg"],
    ["image/png", "png"],
    ["image/webp", "webp"],
    ["image/gif", "gif"],
  ])("maps %s to a .%s key", async (contentType, ext) => {
    const { key } = await upload.getPresignedUploadUrl(
      "whatever.html",
      contentType,
    );

    expect(key).toMatch(new RegExp(`^screenshots/[^/]+\\.${ext}$`));
  });

  it("ignores a hostile filename with a disallowed extension", async () => {
    const { key } = await upload.getPresignedUploadUrl(
      "payload.html",
      "image/png",
    );

    expect(key.endsWith(".html")).toBe(false);
    expect(key.endsWith(".png")).toBe(true);
  });

  it("falls back to .bin for an unrecognized content type", async () => {
    const { key } = await upload.getPresignedUploadUrl(
      "file",
      "application/octet-stream",
    );

    expect(key).toMatch(/^screenshots\/[^/]+\.bin$/);
  });

  it("passes the sanitized original filename as object metadata, not the key", async () => {
    await upload.getPresignedUploadUrl("my photo #1!.png", "image/png");

    const command = putObjectCommand.mock.calls[0][0] as {
      Metadata?: Record<string, string>;
    };
    expect(command.Metadata?.["original-filename"]).toBe("my_photo__1_.png");
  });

  it("includes ContentLength in PutObjectCommand when size is provided", async () => {
    await upload.getPresignedUploadUrl(
      "photo.png",
      "image/png",
      "screenshots",
      1048576,
    );

    const command = putObjectCommand.mock.calls[0][0] as {
      ContentLength?: number;
    };
    expect(command.ContentLength).toBe(1048576);
  });

  it("reuses one client for upload and read presigns", async () => {
    await upload.getPresignedUploadUrl("first.png", "image/png");
    await upload.getPresignedReadUrl("screenshots/existing.png");
    await upload.getPresignedUploadUrl("second.webp", "image/webp");

    expect(s3Client).toHaveBeenCalledTimes(1);
    expect(getSignedUrl).toHaveBeenCalledTimes(3);
  });

  it.each([
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET_NAME",
  ])("names a missing %s variable before signing", async (variable) => {
    delete process.env[variable];

    await expect(
      upload.getPresignedReadUrl("screenshots/existing.png"),
    ).rejects.toThrow(`Missing ${variable} environment variable.`);
    expect(s3Client).not.toHaveBeenCalled();
    expect(getSignedUrl).not.toHaveBeenCalled();
  });
});
