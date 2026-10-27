import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, Building2, Check, ChevronDown, LogOut, Moon, Search, Sun, User } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { useAuth } from "@/lib/auth";
import { sessionApi } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { consoleApi, type Notification } from "../api";
import { DropdownContent, DropdownItem, DropdownLabel, DropdownMenu, DropdownSeparator, DropdownTrigger, Popover, PopoverContent, PopoverTrigger } from "../ui/menu";
import { describeError } from "../ui/states";
import { useToast } from "../ui/toast";
import { useTheme } from "./theme";

/** The organizations the signed in person belongs to. With only one there is nothing to switch, so the name is shown without a menu. */
export function OrgSwitcher() {
  const { me } = useAuth();
  const client = useQueryClient();
  const toast = useToast();
  const query = useQuery({ queryKey: ["organizations", me?.user_id], queryFn: ({ signal }) => consoleApi.organizations(signal), enabled: !!me });
  if (!me) return null;
  const orgs = query.data ?? [];
  if (orgs.length < 2) {
    return (
      <div className="flex min-w-0 items-center gap-2 text-sm font-medium" title="Organization">
        <Building2 className="h-4 w-4 shrink-0 text-foreground-muted" aria-hidden="true" />
        <span className="truncate">{me.org_name}</span>
      </div>
    );
  }
  async function pick(orgId: string) {
    if (orgId === me?.org_id) return;
    try {
      await consoleApi.switchOrg(orgId);
      const fresh = await sessionApi.me();
      client.removeQueries({ predicate: (q) => q.queryKey[0] !== "session" });
      client.setQueryData(["session"], fresh);
      toast.success(`Switched to ${fresh.org_name}`);
    } catch (error) {
      toast.error("Could not switch organization", describeError(error));
    }
  }
  return (
    <DropdownMenu>
      <DropdownTrigger asChild>
        <Button variant="ghost" size="sm" className="max-w-[14rem] gap-2" aria-label={`Organization: ${me.org_name}. Switch organization`}>
          <Building2 className="h-4 w-4" aria-hidden="true" />
          <span className="truncate">{me.org_name}</span>
          <ChevronDown className="h-4 w-4" aria-hidden="true" />
        </Button>
      </DropdownTrigger>
      <DropdownContent align="start">
        <DropdownLabel className="px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-foreground-muted">Organizations</DropdownLabel>
        {orgs.map((org) => (
          <DropdownItem key={org.org_id} onSelect={() => void pick(org.org_id)}>
            <span className="flex-1 truncate">{org.name}</span>
            <span className="text-xs capitalize text-foreground-muted">{org.role}</span>
            {org.org_id === me.org_id ? <Check className="h-4 w-4 text-primary" aria-label="Current" /> : null}
          </DropdownItem>
        ))}
      </DropdownContent>
    </DropdownMenu>
  );
}

const SEEN = "legacylift.notifications-seen";

function readSeen(): number {
  try {
    return Number(window.localStorage.getItem(SEEN)) || 0;
  } catch {
    return 0;
  }
}

const dot: Record<Notification["kind"], string> = { success: "bg-success", error: "bg-danger", info: "bg-primary", action: "bg-warning", waiting: "bg-warning" };
const kindWord: Record<Notification["kind"], string> = { success: "Done", error: "Failed", info: "Info", action: "Needs you", waiting: "Waiting" };

/** What happened lately to the organization's jobs. The count is of events newer than the last time the list was opened. */
export function NotificationsBell() {
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState(readSeen);
  const query = useQuery({ queryKey: ["notifications"], queryFn: ({ signal }) => consoleApi.notifications(signal), refetchInterval: 30_000, staleTime: 15_000 });
  const items = useMemo(() => query.data ?? [], [query.data]);
  const unread = items.filter((n) => n.id > seen).length;
  useEffect(() => {
    if (!open || items.length === 0) return;
    const newest = Math.max(...items.map((n) => n.id));
    try {
      window.localStorage.setItem(SEEN, String(newest));
    } catch {
      /* the count then starts again next time */
    }
  }, [open, items]);
  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (!next) setSeen(readSeen());
        setOpen(next);
      }}
    >
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="relative" aria-label={unread > 0 ? `Notifications, ${unread} new` : "Notifications"}>
          <Bell className="h-5 w-5" aria-hidden="true" />
          {unread > 0 ? <span className="absolute right-1.5 top-1.5 h-2.5 w-2.5 rounded-full bg-danger ring-2 ring-background" aria-hidden="true" /> : null}
        </Button>
      </PopoverTrigger>
      <PopoverContent>
        <div className="border-b border-border px-4 py-3">
          <h2 className="font-semibold">Notifications</h2>
        </div>
        {query.isError ? (
          <p role="alert" className="p-4 text-sm text-danger">
            {describeError(query.error)}
          </p>
        ) : items.length === 0 ? (
          <p className="p-6 text-center text-sm text-foreground-muted">Nothing yet. Updates about your migrations appear here.</p>
        ) : (
          <ul className="max-h-96 divide-y divide-border overflow-y-auto">
            {items.map((n) => (
              <li key={n.id}>
                <Link to={`/app/migrations/${n.job_id}`} onClick={() => setOpen(false)} className="flex gap-3 px-4 py-3 hover:bg-surface-muted">
                  <span className={cn("mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full", dot[n.kind])} aria-hidden="true" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-semibold">
                      {kindWord[n.kind]}: {n.source_name ?? "A migration"}
                    </span>
                    <span className="block text-sm text-foreground-muted">{n.message ?? n.to_status}</span>
                    <span className="block text-xs text-foreground-subtle">{timeAgo(n.created_at)}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  );
}

export function ThemeToggle() {
  const { resolved, toggle } = useTheme();
  return (
    <Button variant="ghost" size="icon" onClick={toggle} aria-label={resolved === "dark" ? "Switch to the light theme" : "Switch to the dark theme"}>
      {resolved === "dark" ? <Sun className="h-5 w-5" aria-hidden="true" /> : <Moon className="h-5 w-5" aria-hidden="true" />}
    </Button>
  );
}

export function UserMenu() {
  const { me, logout } = useAuth();
  const navigate = useNavigate();
  if (!me) return null;
  async function signOut() {
    // Leave first: ending the session re-renders the protected route, which would send a signed out person to the sign in page.
    navigate("/", { replace: true });
    await logout();
  }
  return (
    <DropdownMenu>
      <DropdownTrigger asChild>
        <Button variant="ghost" size="sm" className="gap-2" aria-label={`Account menu for ${me.full_name}`}>
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-primary-soft text-primary-ink" aria-hidden="true">
            <User className="h-4 w-4" />
          </span>
          <span className="hidden max-w-[10rem] truncate md:inline">{me.full_name}</span>
          <ChevronDown className="h-4 w-4" aria-hidden="true" />
        </Button>
      </DropdownTrigger>
      <DropdownContent>
        <div className="px-3 py-2">
          <p className="truncate font-semibold">{me.full_name}</p>
          <p className="truncate text-sm text-foreground-muted">{me.email}</p>
          <p className="mt-1 text-xs font-semibold capitalize text-teal-text">{me.role}</p>
        </div>
        <DropdownSeparator />
        <DropdownItem onSelect={() => void signOut()}>
          <LogOut className="h-4 w-4" aria-hidden="true" />
          Sign out
        </DropdownItem>
      </DropdownContent>
    </DropdownMenu>
  );
}

export function PaletteButton({ onOpen }: { onOpen: () => void }) {
  const mac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
  return (
    <Button variant="outline" size="sm" onClick={onOpen} className="gap-2 text-foreground-muted" aria-keyshortcuts="Control+K Meta+K">
      <Search className="h-4 w-4" aria-hidden="true" />
      <span className="sr-only sm:not-sr-only">Search or jump to</span>
      <kbd aria-hidden="true" className="hidden rounded border border-border-strong px-1.5 text-xs sm:inline">
        {mac ? "⌘K" : "Ctrl K"}
      </kbd>
    </Button>
  );
}
