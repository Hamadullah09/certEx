import { z } from "zod";

import { userRoleSchema } from "@/lib/schemas/auth";

/**
 * The people in an office.
 *
 * Nothing here carries a password or anything derived from one; passwords only ever
 * travel inbound.
 */
export const userSummarySchema = z.object({
  id: z.string().uuid(),
  email: z.string(),
  /** Null on an account made before names existed, or by a script. */
  full_name: z.string().nullish(),
  role: userRoleSchema,
  is_active: z.boolean(),
  last_login_at: z.string().nullish(),
  /**
   * Set when this person said they had forgotten their password.
   *
   * There is no mail server in a records office, so nothing can be sent to them. The
   * request surfaces here instead and an administrator sets a new password - which is
   * how it would be handled anyway, by walking over.
   */
  password_reset_requested_at: z.string().nullish(),
  created_at: z.string(),
});
export type UserSummary = z.infer<typeof userSummarySchema>;

export const userListSchema = z.array(userSummarySchema);

/** The shortest password the server will take. Checked here so the message is instant. */
export const MIN_PASSWORD_LENGTH = 10;

/**
 * What to call somebody on screen.
 *
 * The name when there is one. Otherwise the part of the address before the @, which
 * reads as a name far more often than the whole address does.
 */
export function displayName(user: {
  full_name?: string | null;
  email: string;
}): string {
  const named = user.full_name?.trim();
  if (named) return named;
  return user.email.split("@")[0] ?? user.email;
}

/** Two letters for the avatar: initials where there is a name, otherwise the address. */
export function initials(user: { full_name?: string | null; email: string }): string {
  const name = displayName(user);
  const words = name.split(/[\s._-]+/).filter(Boolean);
  if (words.length >= 2) {
    return `${words[0]![0] ?? ""}${words[1]![0] ?? ""}`.toUpperCase();
  }
  return name.slice(0, 2).toUpperCase();
}

export const ROLE_LABEL: Record<z.infer<typeof userRoleSchema>, string> = {
  ADMIN: "Administrator",
  OPERATOR: "Operator",
  VIEWER: "Viewer",
};

export const ROLE_DETAIL: Record<z.infer<typeof userRoleSchema>, string> = {
  ADMIN: "Everything, including managing people and settings.",
  OPERATOR: "Upload certificates, correct and approve what was read.",
  VIEWER: "Look things up and download. Cannot change anything.",
};
