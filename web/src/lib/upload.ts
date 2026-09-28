/**
 * Pure helpers for the upload client, kept free of React and the network so the
 * rules they encode - which files are chunked, how a file is identified across a
 * browser restart, how passwords travel - are unit-testable.
 */

/** Files above this size go through the resumable, chunked protocol. */
export const CHUNKED_UPLOAD_THRESHOLD_BYTES = 8 * 1024 * 1024;

/** How many small files share one multipart request. */
export const SMALL_FILE_GROUP_SIZE = 5;

export function isChunked(size: number): boolean {
  return size > CHUNKED_UPLOAD_THRESHOLD_BYTES;
}

/**
 * 64-bit FNV-1a over the UTF-16 code units of `text`, as hex.
 *
 * Not a security hash - it only has to make two different file names in one
 * batch produce different ids, and to do so identically after a browser restart.
 */
export function fnv1a64(text: string): string {
  let hash = 0xcbf29ce484222325n;
  const prime = 0x100000001b3n;
  const mask = 0xffffffffffffffffn;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= BigInt(text.charCodeAt(index));
    hash = (hash * prime) & mask;
  }
  return hash.toString(16).padStart(16, "0");
}

/**
 * A stable id for a file, so re-adding it after a crash resumes the same upload.
 *
 * Built from what the browser reports about the file - name, size and last
 * modified time - and restricted to the characters the server accepts. The name
 * is hashed rather than embedded so Urdu or spaces in a filename cannot break it.
 */
export function clientFileId(file: { name: string; size: number; lastModified: number }): string {
  return `f-${file.size}-${file.lastModified}-${fnv1a64(file.name)}`;
}

export interface ChunkRange {
  offset: number;
  end: number;
}

/**
 * The chunks still to send, starting where the server says it has bytes up to.
 *
 * Every chunk but the last is exactly `chunkSize`, which is what the server
 * requires to map a chunk onto a storage part.
 */
export function remainingChunks(size: number, chunkSize: number, receivedBytes: number): ChunkRange[] {
  if (chunkSize <= 0) throw new Error("chunkSize must be positive");
  const ranges: ChunkRange[] = [];
  // A complete file's count lands mid-chunk (the last chunk is short), so rounding
  // it down to a boundary would resend that final chunk.
  if (receivedBytes >= size) return ranges;
  const start = Math.max(0, Math.floor(receivedBytes / chunkSize) * chunkSize);
  for (let offset = start; offset < size; offset += chunkSize) {
    ranges.push({ offset, end: Math.min(offset + chunkSize, size) });
  }
  return ranges;
}

export interface PasswordEntry {
  name: string;
  password?: string | undefined;
}

/**
 * The `passwords` form field for a group of files, or null when none has one.
 *
 * Keys are the names the files are uploaded under, which is what the server
 * matches against.
 */
export function passwordsField(entries: PasswordEntry[]): string | null {
  const map: Record<string, string> = {};
  for (const entry of entries) {
    if (entry.password) map[entry.name] = entry.password;
  }
  return Object.keys(map).length > 0 ? JSON.stringify(map) : null;
}

/** Error codes a user can fix by supplying a password and retrying. */
export function needsPassword(errorCode: string | null | undefined): boolean {
  return errorCode === "document_encrypted";
}
