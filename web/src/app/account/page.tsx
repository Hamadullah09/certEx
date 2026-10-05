"use client";

import * as React from "react";
import { KeyRound, UserRound } from "lucide-react";
import { toast } from "sonner";

import { AppShell } from "@/components/app-shell";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader } from "@/components/ui/page-header";
import { useSession } from "@/hooks/use-session";
import { useChangeOwnPassword, useUpdateUser } from "@/hooks/use-users";
import { ApiError } from "@/lib/api";
import { MIN_PASSWORD_LENGTH, ROLE_DETAIL, ROLE_LABEL } from "@/lib/schemas/users";

/**
 * Your own account: what you are called, and your password.
 *
 * Changing your own password needs the current one even though you are already signed
 * in, because "signed in" and "is the person who owns this account" stop being the same
 * thing the moment somebody walks away from an unlocked machine - which, in a shared
 * records office, is the normal way a workstation spends its afternoon.
 */
export default function AccountPage() {
  const { data: session } = useSession();
  const updateUser = useUpdateUser();
  const changePassword = useChangeOwnPassword();

  const [name, setName] = React.useState("");
  const seeded = React.useRef(false);
  React.useEffect(() => {
    if (seeded.current || !session) return;
    seeded.current = true;
    setName(session.user.full_name ?? "");
  }, [session]);

  const [current, setCurrent] = React.useState("");
  const [next, setNext] = React.useState("");
  const [again, setAgain] = React.useState("");

  const mismatch = again.length > 0 && next !== again;
  const tooShort = next.length > 0 && next.length < MIN_PASSWORD_LENGTH;
  const canChange =
    current.length > 0 && next.length >= MIN_PASSWORD_LENGTH && next === again &&
    !changePassword.isPending;

  if (!session) return <AppShell>{null}</AppShell>;

  return (
    <AppShell>
      <PageHeader
        icon={UserRound}
        title="My account"
        description="What you are called here, and the password you sign in with."
      />

      <Card className="mt-8">
        <CardHeader className="pb-4">
          <CardTitle>Your name</CardTitle>
          <CardDescription>
            Shown beside anything you approve or correct, so colleagues can tell who did
            what. Your email address and what you are allowed to do are set by an
            administrator.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="max-w-md">
            <Label htmlFor="full-name">Name</Label>
            <Input
              id="full-name"
              className="mt-1.5"
              placeholder="Ayesha Noor"
              value={name}
              disabled={updateUser.isPending}
              onChange={(event) => setName(event.target.value)}
            />
          </div>

          <dl className="grid gap-3 sm:grid-cols-2">
            <div>
              <dt className="text-sm font-semibold text-muted-foreground">Email</dt>
              <dd className="text-base text-foreground">{session.user.email}</dd>
            </div>
            <div>
              <dt className="text-sm font-semibold text-muted-foreground">Role</dt>
              <dd className="text-base text-foreground">
                {ROLE_LABEL[session.user.role]}
                <span className="block text-sm text-muted-foreground">
                  {ROLE_DETAIL[session.user.role]}
                </span>
              </dd>
            </div>
          </dl>

          <Button
            size="lg"
            disabled={updateUser.isPending || name.trim() === (session.user.full_name ?? "")}
            onClick={() =>
              updateUser.mutate(
                { id: session.user.id, full_name: name.trim() },
                {
                  onSuccess: () => toast.success("Your name was saved."),
                  onError: (error) =>
                    toast.error(
                      error instanceof ApiError ? error.userMessage : "It could not be saved.",
                    ),
                },
              )
            }
          >
            {updateUser.isPending ? "Saving…" : "Save my name"}
          </Button>
        </CardContent>
      </Card>

      <Card className="mt-6">
        <CardHeader className="pb-4">
          <CardTitle className="flex items-center gap-2">
            <KeyRound aria-hidden="true" className="size-5 text-primary" />
            Change your password
          </CardTitle>
          <CardDescription>
            You need your current one. If you have forgotten it, ask an administrator in
            this office to set a new one for you.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid max-w-xl gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <Label htmlFor="current-password">Current password</Label>
              <Input
                id="current-password"
                type="password"
                autoComplete="current-password"
                className="mt-1.5"
                value={current}
                onChange={(event) => setCurrent(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="new-password">New password</Label>
              <Input
                id="new-password"
                type="password"
                autoComplete="new-password"
                className="mt-1.5"
                value={next}
                onChange={(event) => setNext(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="repeat-password">New password again</Label>
              <Input
                id="repeat-password"
                type="password"
                autoComplete="new-password"
                className="mt-1.5"
                value={again}
                onChange={(event) => setAgain(event.target.value)}
              />
            </div>
          </div>

          <p className="text-base text-muted-foreground">
            At least {MIN_PASSWORD_LENGTH} characters. A few unrelated words is both longer
            and easier to remember than something with symbols in it.
          </p>

          {tooShort ? (
            <Alert variant="warning">
              <AlertTitle>That is too short</AlertTitle>
              <AlertDescription>
                Use at least {MIN_PASSWORD_LENGTH} characters.
              </AlertDescription>
            </Alert>
          ) : null}
          {mismatch ? (
            <Alert variant="warning">
              <AlertTitle>The two do not match</AlertTitle>
              <AlertDescription>Type the same new password in both boxes.</AlertDescription>
            </Alert>
          ) : null}

          <Button
            size="lg"
            disabled={!canChange}
            onClick={() =>
              changePassword.mutate(
                { current_password: current, new_password: next },
                {
                  onSuccess: () => {
                    toast.success("Your password was changed.");
                    setCurrent("");
                    setNext("");
                    setAgain("");
                  },
                  onError: (error) =>
                    toast.error(
                      error instanceof ApiError
                        ? error.userMessage
                        : "The password could not be changed.",
                    ),
                },
              )
            }
          >
            {changePassword.isPending ? "Changing…" : "Change my password"}
          </Button>
        </CardContent>
      </Card>
    </AppShell>
  );
}
