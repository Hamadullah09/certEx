"use client";

import * as React from "react";
import Link from "next/link";
import { AlertCircle, FileStack, Loader2 } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useLogin } from "@/hooks/use-session";
import { ApiError } from "@/lib/api";
import { loginRequestSchema } from "@/lib/schemas/auth";

const appName = process.env.NEXT_PUBLIC_APP_NAME ?? "CertExtract";

export default function LoginPage() {
  const login = useLogin();
  const [fieldErrors, setFieldErrors] = React.useState<Record<string, string>>({});

  function handleSubmit(event: React.FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    const data = new FormData(event.currentTarget);

    const parsed = loginRequestSchema.safeParse({
      email: String(data.get("email") ?? ""),
      password: String(data.get("password") ?? ""),
    });

    if (!parsed.success) {
      const errors: Record<string, string> = {};
      for (const issue of parsed.error.issues) {
        const field = issue.path[0];
        if (typeof field === "string" && !errors[field]) errors[field] = issue.message;
      }
      setFieldErrors(errors);
      return;
    }

    setFieldErrors({});
    login.mutate(parsed.data);
  }

  const serverError = login.error;
  const isRateLimited = serverError instanceof ApiError && serverError.status === 429;

  return (
    <main id="main" className="flex min-h-dvh items-center justify-center px-4 py-14">
      <div className="w-full max-w-md">
        {/* The mark sits above the card rather than inside it, so the first
            thing on screen at a shared terminal is which system this is. */}
        <div className="mb-7 flex flex-col items-center gap-3 text-center">
          <span
            aria-hidden="true"
            className="flex size-16 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-lift"
          >
            <FileStack className="size-9" />
          </span>
          <p className="text-2xl font-bold tracking-tight text-foreground">{appName}</p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-2xl">Sign in</CardTitle>
            <CardDescription>
              Use the account your workspace administrator gave you.
            </CardDescription>
          </CardHeader>

          <form onSubmit={handleSubmit} noValidate>
            <CardContent className="space-y-6">
              {serverError ? (
                <Alert variant={isRateLimited ? "warning" : "destructive"}>
                  <AlertCircle aria-hidden="true" />
                  <AlertTitle>
                    {serverError instanceof ApiError ? serverError.message : "Sign-in failed"}
                  </AlertTitle>
                  <AlertDescription>
                    {serverError instanceof ApiError
                      ? (serverError.remediation ?? "Check your details and try again.")
                      : "Could not reach the server. Check your connection and try again."}
                  </AlertDescription>
                </Alert>
              ) : null}

              <div className="space-y-2.5">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  name="email"
                  type="email"
                  autoComplete="username"
                  autoFocus
                  required
                  aria-invalid={Boolean(fieldErrors.email)}
                  aria-describedby={fieldErrors.email ? "email-error" : undefined}
                  disabled={login.isPending}
                />
                {fieldErrors.email ? (
                  <p
                    id="email-error"
                    className="flex items-start gap-2 text-base font-semibold text-destructive"
                  >
                    <AlertCircle aria-hidden="true" className="mt-0.5 size-5 shrink-0" />
                    {fieldErrors.email}
                  </p>
                ) : null}
              </div>

              <div className="space-y-2.5">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  required
                  aria-invalid={Boolean(fieldErrors.password)}
                  aria-describedby={fieldErrors.password ? "password-error" : undefined}
                  disabled={login.isPending}
                />
                {fieldErrors.password ? (
                  <p
                    id="password-error"
                    className="flex items-start gap-2 text-base font-semibold text-destructive"
                  >
                    <AlertCircle aria-hidden="true" className="mt-0.5 size-5 shrink-0" />
                    {fieldErrors.password}
                  </p>
                ) : null}
              </div>
            </CardContent>

            <CardFooter className="flex-col items-stretch gap-4">
              <Button type="submit" size="lg" className="w-full" disabled={login.isPending}>
                {login.isPending ? (
                  <>
                    <Loader2 className="animate-spin" aria-hidden="true" />
                    Signing in
                  </>
                ) : (
                  "Sign in"
                )}
              </Button>
              <Link
                href="/forgot-password"
                className="text-center text-base font-semibold text-primary underline decoration-2 underline-offset-4"
              >
                I have forgotten my password
              </Link>
            </CardFooter>
          </form>
        </Card>
      </div>
    </main>
  );
}
