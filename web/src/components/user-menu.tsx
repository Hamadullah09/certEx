"use client";

import * as React from "react";
import Link from "next/link";
import { Building2, KeyRound, LogOut, Settings, Users } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useLogout } from "@/hooks/use-session";
import type { SessionResponse } from "@/lib/schemas/auth";
import { ROLE_LABEL, displayName, initials } from "@/lib/schemas/users";
import { cn } from "@/lib/utils";

/**
 * Who is signed in, behind their initials.
 *
 * The header used to print the workspace name, the email address and the role as three
 * lines of permanent text. That is a lot of room for something nobody reads twice, and
 * it pushed the categories - the thing people actually click - into a corner.
 *
 * Opening it is how you reach everything about your own account, and for an
 * administrator, everyone else's.
 */
export function UserMenu({ session }: { session: SessionResponse }) {
  const [open, setOpen] = React.useState(false);
  const container = React.useRef<HTMLDivElement>(null);
  const logout = useLogout();

  const name = displayName(session.user);
  const isAdmin = session.user.role === "ADMIN";

  // Closed by a click anywhere else, and by Escape. Without both, a menu opened by
  // mistake has to be dismissed by clicking the button again, which nobody guesses.
  React.useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (!container.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const items = [
    { href: "/account", label: "My account", detail: "Your name and password", Icon: KeyRound },
    ...(isAdmin
      ? [
          {
            href: "/settings/users",
            label: "Users",
            detail: "Add users and set what they can do",
            Icon: Users,
          },
          {
            href: "/settings",
            label: "Settings",
            detail: "Office name and how certificates are read",
            Icon: Settings,
          },
        ]
      : []),
  ];

  return (
    <div ref={container} className="relative">
      <Button
        variant="outline"
        className="gap-2 px-2.5"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((current) => !current)}
      >
        <span
          aria-hidden="true"
          className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary text-sm font-bold text-primary-foreground"
        >
          {initials(session.user)}
        </span>
        <span className="hidden max-w-32 truncate sm:inline">{name}</span>
      </Button>

      {open ? (
        <div
          role="menu"
          className="absolute right-0 z-50 mt-2 w-72 overflow-hidden rounded-xl border-2 border-border bg-card shadow-lg"
        >
          <div className="border-b border-border-subtle p-4">
            <p className="truncate text-base font-bold text-foreground">{name}</p>
            <p className="truncate text-sm text-muted-foreground">{session.user.email}</p>
            <p className="mt-1.5 text-sm font-semibold text-primary">
              {ROLE_LABEL[session.user.role]}
            </p>
            <p className="mt-2 flex items-center gap-1.5 truncate text-sm text-muted-foreground">
              <Building2 aria-hidden="true" className="size-4 shrink-0" />
              {session.workspace.name}
            </p>
          </div>

          <ul className="p-1.5">
            {items.map(({ href, label, detail, Icon }) => (
              <li key={href}>
                <Link
                  href={href}
                  role="menuitem"
                  onClick={() => setOpen(false)}
                  className="flex min-h-12 items-center gap-3 rounded-lg px-3 py-2 hover:bg-accent hover:text-accent-foreground"
                >
                  <Icon aria-hidden="true" className="size-5 shrink-0 text-muted-foreground" />
                  <span className="min-w-0">
                    <span className="block text-base font-semibold">{label}</span>
                    <span className="block text-sm text-muted-foreground">{detail}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>

          <div className={cn("border-t border-border-subtle p-1.5")}>
            <button
              type="button"
              role="menuitem"
              disabled={logout.isPending}
              onClick={() => logout.mutate()}
              className="flex min-h-12 w-full items-center gap-3 rounded-lg px-3 py-2 text-base font-semibold hover:bg-accent hover:text-accent-foreground"
            >
              <LogOut aria-hidden="true" className="size-5 shrink-0 text-muted-foreground" />
              Sign out
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
