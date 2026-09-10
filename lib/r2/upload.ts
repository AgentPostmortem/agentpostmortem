import {
  S3Client,
  PutObjectCommand,
  GetObjectCommand,
} from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";
import { randomUUID } from "crypto";
import { getR2PublicBaseUrl } from "@/lib/utils/urls";

const EXT_BY_CONTENT_TYPE: Record<string, string> = {
  "image/jpeg": "jpg",
  "image/png": "png",
  "image/webp": "webp",
  "image/gif": "gif",
};

/**
 * Maps a validated content type to a storage extension. Never derive the
 * extension from a user-supplied filename — it isn't checked against the
 * allowlist the route validates `contentType` with.
 */
function extensionForContentType(contentType: string): string {
  return EXT_BY_CONTENT_TYPE[contentType] ?? "bin";
}

/** Strips the filename down to characters safe for an S3 metadata header. */
function sanitizeFilenameForMetadata(filename: string): string {
  return filename.replace(/[^\w.-]/g, "_").slice(0, 255);
}

let r2: { client: S3Client; bucketName: string } | undefined;

function requiredEnv(name: string): string {
  const value = process.env[name];
  if (!value?.trim()) {
    throw new Error(`Missing ${name} environment variable.`);
  }
  return value;
}

function getR2Client(): { client: S3Client; bucketName: string } {
  if (r2) return r2;

  const accountId = requiredEnv("R2_ACCOUNT_ID");
  const accessKeyId = requiredEnv("R2_ACCESS_KEY_ID");
  const secretAccessKey = requiredEnv("R2_SECRET_ACCESS_KEY");
  const bucketName = requiredEnv("R2_BUCKET_NAME");
  const client = new S3Client({
    region: "auto",
    endpoint: `https://${accountId}.r2.cloudflarestorage.com`,
    credentials: { accessKeyId, secretAccessKey },
  });
  r2 = { client, bucketName };
  return r2;
}

interface PresignUploadResult {
  /** Presigned PUT URL — forward to client for direct upload */
  uploadUrl: string;
  /** Public URL of the object after upload */
  publicUrl: string;
  /** Object key stored in R2 */
  key: string;
}

/**
 * Generate a presigned PUT URL for direct browser-to-R2 upload.
 * Expires in 5 minutes.
 */
export async function getPresignedUploadUrl(
  filename: string,
  contentType: string,
  folder = "screenshots",
  contentLength?: number,
): Promise<PresignUploadResult> {
  const ext = extensionForContentType(contentType);
  const key = `${folder}/${randomUUID()}.${ext}`;

  const { client, bucketName } = getR2Client();
  const command = new PutObjectCommand({
    Bucket: bucketName,
    Key: key,
    ContentType: contentType,
    ...(contentLength != null ? { ContentLength: contentLength } : {}),
    Metadata: { "original-filename": sanitizeFilenameForMetadata(filename) },
  });

  const uploadUrl = await getSignedUrl(client, command, { expiresIn: 300 });
  const publicBaseUrl = getR2PublicBaseUrl();
  if (!publicBaseUrl) {
    throw new Error(
      "Missing NEXT_PUBLIC_R2_PUBLIC_URL or R2_PUBLIC_URL environment variable.",
    );
  }

  const publicUrl = `${publicBaseUrl}/${key}`;

  return { uploadUrl, publicUrl, key };
}

/**
 * Generate a presigned GET URL for a private R2 object.
 * Expires in 1 hour.
 */
export async function getPresignedReadUrl(key: string): Promise<string> {
  const { client, bucketName } = getR2Client();
  const command = new GetObjectCommand({
    Bucket: bucketName,
    Key: key,
  });

  return getSignedUrl(client, command, { expiresIn: 3600 });
}
