"use client";

import * as React from "react";
import Link from "next/link";
import { CheckCircle2, FileStack, Loader2 } from "lucide-react";

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
import { useForgotPassword } from "@/hooks/use-users";

const appName = process.env.NEXT_PUBLIC_APP_NAME ?? "CertExtract";

/**
 * Saying you have forgotten your password.
 *
 * There is no mail server in a records office, so there is no link to send. What this
 * does instead is tell the administrators: the request shows against your name in their
 * list of people, and one of them sets a new password and tells you what it is.
 *
 * That is slower than an email, and better evidence of who you are - the person setting
 * it can see you.
 *
 * The screen says the same thing whatever address is typed, matching the server. An
 * answer that differed would turn this page into a way of finding out who works here.
 */
export default function ForgotPasswordPage() {
  const [email, setEmail] = React.useState("");
  const request = useForgotPassword();

  if (request.isSuccess) {
    return (
      <main className="flex min-h-dvh items-center justify-center px-4 py-12">
        <div className="w-full max-w-md">
          <Card>
            <CardHeader className="items-center text-center">
              <span
                aria-hidden="true"
                className="flex size-14 items-center justify-center rounded-xl bg-success-surface text-success-surface-foreground"
              >
                <CheckCircle2 className="size-7" />
              </span>
              <CardTitle className="text-2xl">Your office has been told</CardTitle>
              <CardDescription>
                If that address belongs to an account here, it now shows on the
                administrator&rsquo;s list of people as having forgotten its password.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Alert>
                <AlertTitle>What happens next</AlertTitle>
                <AlertDescription>
                  Ask an administrator in this office to set you a new password. Nothing
                  is emailed - they set it and tell you in person, which is why nobody
                  needs to check a mailbox.
                </AlertDescription>
              </Alert>
            </CardContent>
            <CardFooter>
              <Button asChild size="lg" className="w-full">
                <Link href="/login">Back to sign in</Link>
              </Button>
            </CardFooter>
          </Card>
        </div>
      </main>
    );
  }

  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-12">
      <div className="w-full max-w-md">
        <div className="mb-8 flex flex-col items-center gap-3">
          <span
            aria-hidden="true"
            className="flex size-14 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-soft"
          >
            <FileStack className="size-7" />
          </span>
          <p className="text-xl font-bold tracking-tight">{appName}</p>
        </div>

        <Card>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (email.trim()) request.mutate(email.trim());
            }}
          >
            <CardHeader>
              <CardTitle className="text-2xl">Forgotten your password</CardTitle>
              <CardDescription>
                Type the address you sign in with. An administrator in your office will
                set you a new password - there is no email sent, so you do not need to
                check a mailbox.
              </CardDescription>
            </CardHeader>

            <CardContent>
              <Label htmlFor="email">Email address</Label>
              <Input
                id="email"
                name="email"
                type="email"
                autoComplete="username"
                className="mt-1.5"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </CardContent>

            <CardFooter className="flex-col items-stretch gap-4">
              <Button
                type="submit"
                size="lg"
                className="w-full"
                disabled={!email.trim() || request.isPending}
              >
                {request.isPending ? (
                  <>
                    <Loader2 className="animate-spin" aria-hidden="true" />
                    Sending
                  </>
                ) : (
                  "Tell my office"
                )}
              </Button>
              <Link
                href="/login"
                className="text-center text-base font-semibold text-primary underline decoration-2 underline-offset-4"
              >
                Back to sign in
              </Link>
            </CardFooter>
          </form>
        </Card>
      </div>
    </main>
  );
}
