import { describe, expect, it } from "vitest";

import {
  CHUNKED_UPLOAD_THRESHOLD_BYTES,
  clientFileId,
  fnv1a64,
  isChunked,
  needsPassword,
  passwordsField,
  remainingChunks,
} from "@/lib/upload";

const MIB = 1024 * 1024;

describe("isChunked", () => {
  it("chunks only files above the threshold", () => {
    expect(isChunked(CHUNKED_UPLOAD_THRESHOLD_BYTES)).toBe(false);
    expect(isChunked(CHUNKED_UPLOAD_THRESHOLD_BYTES + 1)).toBe(true);
  });
});

describe("clientFileId", () => {
  const file = { name: "Ayesha birth.pdf", size: 1234, lastModified: 1_700_000_000_000 };

  it("is stable for the same file", () => {
    expect(clientFileId(file)).toBe(clientFileId({ ...file }));
  });

  it("differs when any identifying property differs", () => {
    const base = clientFileId(file);
    expect(clientFileId({ ...file, name: "other.pdf" })).not.toBe(base);
    expect(clientFileId({ ...file, size: 1235 })).not.toBe(base);
    expect(clientFileId({ ...file, lastModified: 1 })).not.toBe(base);
  });

  it("only uses characters the server accepts, even for Urdu names", () => {
    const id = clientFileId({ ...file, name: "پیدائش سرٹیفکیٹ.pdf" });
    expect(id).toMatch(/^[A-Za-z0-9_.:-]+$/);
    expect(id.length).toBeLessThanOrEqual(128);
  });
});

describe("fnv1a64", () => {
  it("matches the reference vectors", () => {
    expect(fnv1a64("")).toBe("cbf29ce484222325");
    expect(fnv1a64("a")).toBe("af63dc4c8601ec8c");
  });
});

describe("remainingChunks", () => {
  it("splits a file into full chunks plus a final partial chunk", () => {
    expect(remainingChunks(20 * MIB, 8 * MIB, 0)).toEqual([
      { offset: 0, end: 8 * MIB },
      { offset: 8 * MIB, end: 16 * MIB },
      { offset: 16 * MIB, end: 20 * MIB },
    ]);
  });

  it("resumes from the server's received byte count", () => {
    expect(remainingChunks(20 * MIB, 8 * MIB, 8 * MIB)).toEqual([
      { offset: 8 * MIB, end: 16 * MIB },
      { offset: 16 * MIB, end: 20 * MIB },
    ]);
  });

  it("returns nothing once every byte has arrived", () => {
    expect(remainingChunks(20 * MIB, 8 * MIB, 20 * MIB)).toEqual([]);
  });

  it("treats an exact multiple as having no trailing chunk", () => {
    expect(remainingChunks(16 * MIB, 8 * MIB, 0)).toHaveLength(2);
  });
});

describe("passwordsField", () => {
  it("maps only the files that have a password", () => {
    const field = passwordsField([
      { name: "locked.pdf", password: "letmein" },
      { name: "open.pdf" },
    ]);
    expect(field).not.toBeNull();
    expect(JSON.parse(field as string)).toEqual({ "locked.pdf": "letmein" });
  });

  it("is null when no file has a password", () => {
    expect(passwordsField([{ name: "a.pdf" }, { name: "b.pdf", password: "" }])).toBeNull();
  });
});

describe("needsPassword", () => {
  it("recognises only the encrypted-document code", () => {
    expect(needsPassword("document_encrypted")).toBe(true);
    expect(needsPassword("document_corrupt")).toBe(false);
    expect(needsPassword(null)).toBe(false);
  });
});
