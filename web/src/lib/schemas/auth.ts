import { z } from "zod";

export const userRoleSchema = z.enum(["ADMIN", "OPERATOR", "VIEWER"]);
export type UserRole = z.infer<typeof userRoleSchema>;

export const userProfileSchema = z.object({
  id: z.string().uuid(),
  email: z.string().email(),
  role: userRoleSchema,
  workspace_id: z.string().uuid(),
  is_active: z.boolean(),
  last_login_at: z.string().datetime({ offset: true }).nullable().optional(),
});
export type UserProfile = z.infer<typeof userProfileSchema>;

export const workspaceSummarySchema = z.object({
  id: z.string().uuid(),
  name: z.string(),
});
export type WorkspaceSummary = z.infer<typeof workspaceSummarySchema>;

export const sessionResponseSchema = z.object({
  user: userProfileSchema,
  workspace: workspaceSummarySchema,
  access_expires_at: z.string().datetime({ offset: true }),
  csrf_token: z.string(),
});
export type SessionResponse = z.infer<typeof sessionResponseSchema>;

/** Mirrors the server's LoginRequest so bad input is caught before the round trip. */
export const loginRequestSchema = z.object({
  email: z.string().min(1, "Enter your email address").email("Enter a valid email address"),
  password: z.string().min(1, "Enter your password").max(256),
});
export type LoginRequest = z.infer<typeof loginRequestSchema>;

/** Role ranking, kept in step with certex.enums.UserRole.satisfies. */
const ROLE_RANK: Record<UserRole, number> = { VIEWER: 0, OPERATOR: 1, ADMIN: 2 };

export function roleSatisfies(actual: UserRole, required: UserRole): boolean {
  return ROLE_RANK[actual] >= ROLE_RANK[required];
}
