"use client";

import * as React from "react";
import Link from "next/link";
import { AlertTriangle, ChevronLeft, KeyRound, UserPlus, Users } from "lucide-react";
import { toast } from "sonner";

import { AppShell } from "@/components/app-shell";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useSession } from "@/hooks/use-session";
import { useCreateUser, useSetPassword, useUpdateUser, useUsers } from "@/hooks/use-users";
import { ApiError } from "@/lib/api";
import { USER_ROLES, type UserRole } from "@/lib/schemas/auth";
import {
  MIN_PASSWORD_LENGTH,
  ROLE_DETAIL,
  ROLE_LABEL,
  type UserSummary,
  displayName,
  initials,
} from "@/lib/schemas/users";

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return "never";
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function problemText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.userMessage : fallback;
}

/** Setting somebody's password, which is how a forgotten one is dealt with here. */
function SetPassword({ person, onDone }: { person: UserSummary; onDone: () => void }) {
  const [password, setPassword] = React.useState("");
  const setIt = useSetPassword();
  const usable = password.length >= MIN_PASSWORD_LENGTH;

  return (
    <div className="mt-3 space-y-3 rounded-lg border-2 border-primary-border bg-primary-surface/40 p-4">
      <p className="text-base font-semibold">
        Set a new password for {displayName(person)}
      </p>
      <p className="text-base">
        Type one here and tell them in person. Every device they are signed in on is
        signed out.
      </p>
      <div className="max-w-sm">
        <Label htmlFor={`password-${person.id}`}>New password</Label>
        <Input
          id={`password-${person.id}`}
          type="password"
          autoComplete="new-password"
          className="mt-1.5"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <p className="mt-1 text-sm text-muted-foreground">
          At least {MIN_PASSWORD_LENGTH} characters.
        </p>
      </div>
      <div className="flex flex-wrap gap-3">
        <Button
          disabled={!usable || setIt.isPending}
          onClick={() =>
            setIt.mutate(
              { id: person.id, password },
              {
                onSuccess: () => {
                  toast.success(`A new password was set for ${displayName(person)}.`);
                  setPassword("");
                  onDone();
                },
                onError: (error) =>
                  toast.error(problemText(error, "The password could not be set.")),
              },
            )
          }
        >
          {setIt.isPending ? "Setting…" : "Set it"}
        </Button>
        <Button variant="outline" disabled={setIt.isPending} onClick={onDone}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function PersonRow({ person, isMe }: { person: UserSummary; isMe: boolean }) {
  const [settingPassword, setSettingPassword] = React.useState(false);
  const update = useUpdateUser();

  return (
    <li className="px-5 py-4">
      <div className="flex flex-wrap items-start gap-x-4 gap-y-3">
        <span
          aria-hidden="true"
          className="flex size-11 shrink-0 items-center justify-center rounded-full bg-primary text-base font-bold text-primary-foreground"
        >
          {initials(person)}
        </span>

        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-2 text-lg font-bold text-foreground">
            {displayName(person)}
            {isMe ? <Badge variant="outline">You</Badge> : null}
            {!person.is_active ? <Badge variant="danger">Switched off</Badge> : null}
            {person.password_reset_requested_at ? (
              <Badge variant="warning" className="gap-1">
                <KeyRound aria-hidden="true" className="size-4" />
                Forgot their password
              </Badge>
            ) : null}
          </p>
          <p className="truncate text-base text-muted-foreground">{person.email}</p>
          <p className="text-sm text-muted-foreground">
            Last signed in {formatWhen(person.last_login_at)}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Label htmlFor={`role-${person.id}`} className="sr-only">
            Role for {displayName(person)}
          </Label>
          <Select
            id={`role-${person.id}`}
            className="w-48"
            value={person.role}
            // Refused by the server too. Changing your own role cannot be undone,
            // because undoing it needs the role you just gave up.
            disabled={isMe || update.isPending}
            onChange={(event) =>
              update.mutate(
                { id: person.id, role: event.target.value as UserRole },
                {
                  onSuccess: () => toast.success(`${displayName(person)} was updated.`),
                  onError: (error) =>
                    toast.error(problemText(error, "That change could not be saved.")),
                },
              )
            }
          >
            {USER_ROLES.map((role) => (
              <option key={role} value={role}>
                {ROLE_LABEL[role]}
              </option>
            ))}
          </Select>

          <Button
            variant="outline"
            disabled={update.isPending}
            onClick={() => setSettingPassword((open) => !open)}
          >
            <KeyRound aria-hidden="true" />
            Set password
          </Button>

          {!isMe ? (
            <Button
              variant="outline"
              disabled={update.isPending}
              onClick={() =>
                update.mutate(
                  { id: person.id, is_active: !person.is_active },
                  {
                    onSuccess: () =>
                      toast.success(
                        person.is_active
                          ? `${displayName(person)} can no longer sign in.`
                          : `${displayName(person)} can sign in again.`,
                      ),
                    onError: (error) =>
                      toast.error(problemText(error, "That change could not be saved.")),
                  },
                )
              }
            >
              {person.is_active ? "Deactivate" : "Activate"}
            </Button>
          ) : null}
        </div>
      </div>

      {person.password_reset_requested_at && !settingPassword ? (
        <p className="mt-2 text-base text-muted-foreground">
          They asked for a new password on{" "}
          {formatWhen(person.password_reset_requested_at)}. Set one above and tell them
          what it is.
        </p>
      ) : null}

      {settingPassword ? (
        <SetPassword person={person} onDone={() => setSettingPassword(false)} />
      ) : null}
    </li>
  );
}

function AddPerson() {
  const [open, setOpen] = React.useState(false);
  const [email, setEmail] = React.useState("");
  const [name, setName] = React.useState("");
  const [role, setRole] = React.useState<UserRole>("OPERATOR");
  const [password, setPassword] = React.useState("");
  const create = useCreateUser();

  const usable = email.includes("@") && password.length >= MIN_PASSWORD_LENGTH;

  if (!open) {
    return (
      <Button size="lg" onClick={() => setOpen(true)}>
        <UserPlus aria-hidden="true" />
        Add user
      </Button>
    );
  }

  return (
    <Card className="border-2 border-primary-border/60">
      <CardHeader className="pb-4">
        <CardTitle>Add a user</CardTitle>
        <CardDescription>
          They sign in with the email address and the password you set here. Tell them
          both in person - nothing is emailed, because this office has no mail server.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid max-w-2xl gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="new-email">Email address</Label>
            <Input
              id="new-email"
              type="email"
              autoComplete="off"
              className="mt-1.5"
              placeholder="ayesha@office.gov.pk"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="new-name">Name</Label>
            <Input
              id="new-name"
              className="mt-1.5"
              placeholder="Ayesha Noor"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </div>
          <div>
            <Label htmlFor="new-role">Role</Label>
            <Select
              id="new-role"
              className="mt-1.5"
              value={role}
              onChange={(event) => setRole(event.target.value as UserRole)}
            >
              {USER_ROLES.map((item) => (
                <option key={item} value={item}>
                  {ROLE_LABEL[item]}
                </option>
              ))}
            </Select>
            <p className="mt-1 text-sm text-muted-foreground">{ROLE_DETAIL[role]}</p>
          </div>
          <div>
            <Label htmlFor="new-user-password">A password to start with</Label>
            <Input
              id="new-user-password"
              type="password"
              autoComplete="new-password"
              className="mt-1.5"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            <p className="mt-1 text-sm text-muted-foreground">
              At least {MIN_PASSWORD_LENGTH} characters. They can change it afterwards.
            </p>
          </div>
        </div>

        <div className="flex flex-wrap gap-3">
          <Button
            size="lg"
            disabled={!usable || create.isPending}
            onClick={() =>
              create.mutate(
                { email: email.trim(), full_name: name.trim() || undefined, role, password },
                {
                  onSuccess: (person) => {
                    toast.success(`${displayName(person)} can now sign in.`);
                    setOpen(false);
                    setEmail("");
                    setName("");
                    setPassword("");
                    setRole("OPERATOR");
                  },
                  onError: (error) =>
                    toast.error(problemText(error, "That person could not be added.")),
                },
              )
            }
          >
            <UserPlus aria-hidden="true" />
            {create.isPending ? "Adding…" : "Add them"}
          </Button>
          <Button
            variant="outline"
            size="lg"
            disabled={create.isPending}
            onClick={() => setOpen(false)}
          >
            Cancel
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

/**
 * Who works in this office and what each of them may do.
 *
 * Administrators only, and the server enforces the same - hiding a screen is a
 * courtesy, never a control.
 */
export default function PeoplePage() {
  const { data: session } = useSession();
  const isAdmin = session?.user.role === "ADMIN";
  const people = useUsers({ enabled: Boolean(session) });

  const waiting = (people.data ?? []).filter((person) => person.password_reset_requested_at);

  return (
    <AppShell>
      <Button variant="ghost" asChild className="-ml-3">
        <Link href="/settings">
          <ChevronLeft aria-hidden="true" />
          Settings
        </Link>
      </Button>

      <PageHeader
        icon={Users}
        title="Users"
        description="Everyone who can sign in to this office, their role, and their passwords."
        actions={isAdmin ? <AddPerson /> : null}
      />

      {!isAdmin ? (
        <Alert className="mt-8">
          <AlertTriangle aria-hidden="true" />
          <AlertTitle>Only an administrator can change these</AlertTitle>
          <AlertDescription>
            You can see who works here. Adding someone, changing what they can do, or
            setting a password needs an administrator.
          </AlertDescription>
        </Alert>
      ) : null}

      {isAdmin && waiting.length > 0 ? (
        <Alert variant="warning" className="mt-8">
          <KeyRound aria-hidden="true" />
          <AlertTitle>
            {waiting.length === 1
              ? "A user has forgotten their password"
              : `${waiting.length} users have forgotten their passwords`}
          </AlertTitle>
          <AlertDescription>
            {waiting.map((person) => displayName(person)).join(", ")}. Nothing was emailed
            to them - set a password below and tell them what it is.
          </AlertDescription>
        </Alert>
      ) : null}

      {people.isError ? (
        <Alert variant="destructive" className="mt-8">
          <AlertTriangle aria-hidden="true" />
          <AlertTitle>The list could not be loaded</AlertTitle>
          <AlertDescription>
            {problemText(people.error, "Try again in a moment.")}
          </AlertDescription>
        </Alert>
      ) : null}

      <Card className="mt-8">
        <CardHeader className="pb-3">
          <CardTitle>
            {people.data
              ? `${people.data.length} ${people.data.length === 1 ? "user" : "users"}`
              : "Loading"}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {people.isPending ? (
            <div className="space-y-2 p-5">
              {[0, 1, 2].map((index) => (
                <Skeleton key={index} className="h-20 w-full" />
              ))}
            </div>
          ) : (
            <ul className="divide-y divide-border-subtle">
              {(people.data ?? []).map((person) => (
                <PersonRow
                  key={person.id}
                  person={person}
                  isMe={person.id === session?.user.id}
                />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </AppShell>
  );
}
