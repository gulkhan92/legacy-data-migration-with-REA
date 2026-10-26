import * as DialogPrimitive from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { CornerDownLeft, FolderKanban, LogOut, Moon, Search, Sun, type LucideIcon } from "lucide-react";
import { useId, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/cn";
import { jobsApi } from "../api";
import { useDebounced } from "../ui/controls";
import { navFor } from "./nav";
import { useTheme } from "./theme";

export type Command = { id: string; label: string; hint?: string; keywords?: string; icon: LucideIcon; run: () => void };

/** Commands whose words all start a word of the label or keywords, best matches (label starts with the text) first. */
export function filterCommands(commands: Command[], query: string): Command[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return commands;
  const score = (c: Command): number | null => {
    const label = c.label.toLowerCase();
    const haystack = `${label} ${(c.keywords ?? "").toLowerCase()}`;
    if (!words.every((w) => haystack.split(/[\s/]+/).some((token) => token.startsWith(w)) || haystack.includes(w))) return null;
    return label.startsWith(words[0] ?? "") ? 0 : 1;
  };
  return commands
    .map((c) => ({ c, s: score(c) }))
    .filter((x): x is { c: Command; s: number } => x.s !== null)
    .sort((a, b) => a.s - b.s)
    .map((x) => x.c);
}

/** The body of the palette. It exists only while the palette is open, so its text and selection start fresh every time. */
function PaletteBody({ close }: { close: () => void }) {
  const { me, logout } = useAuth();
  const navigate = useNavigate();
  const { resolved, toggle } = useTheme();
  const [text, setText] = useState("");
  const [picked, setPicked] = useState(0);
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const debounced = useDebounced(text.trim(), 250);

  const jobs = useQuery({
    queryKey: ["palette-jobs", debounced],
    queryFn: ({ signal }) => jobsApi.list({ q: debounced, limit: 5 }, signal),
    enabled: debounced.length >= 2,
    staleTime: 10_000,
  });

  const go = (to: string) => () => navigate(to);
  const pages: Command[] = me
    ? navFor(me.role).flatMap((g) =>
        g.items.map((i) => ({ id: i.to, label: i.label, hint: g.group, ...(i.keywords ? { keywords: i.keywords } : {}), icon: i.icon, run: go(i.to) })),
      )
    : [];
  const actions: Command[] = [
    {
      id: "theme",
      label: resolved === "dark" ? "Switch to the light theme" : "Switch to the dark theme",
      keywords: "appearance dark light mode",
      icon: resolved === "dark" ? Sun : Moon,
      run: toggle,
    },
    {
      id: "signout",
      label: "Sign out",
      keywords: "log out leave",
      icon: LogOut,
      run: () => {
        navigate("/", { replace: true });
        void logout();
      },
    },
  ];
  // Migrations found by the server already match the text, so they are kept even when their label does not repeat it.
  const found: Command[] = (jobs.data?.items ?? []).map((j) => ({
    id: `job-${j.id}`,
    label: j.source_name ?? "Migration",
    hint: `Migration, ${j.status.toLowerCase().replace("_", " ")}`,
    icon: FolderKanban,
    run: go(`/app/migrations/${j.id}`),
  }));
  const visible = [...filterCommands([...pages, ...actions], text), ...(debounced.length >= 2 ? found : [])];
  const active = Math.min(picked, Math.max(0, visible.length - 1));

  function run(command: Command | undefined) {
    if (!command) return;
    close();
    command.run();
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setPicked(Math.min(visible.length - 1, active + 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setPicked(Math.max(0, active - 1));
    } else if (event.key === "Enter") {
      event.preventDefault();
      run(visible[active]);
    }
  }

  return (
    <>
      <DialogPrimitive.Title className="sr-only">Command palette</DialogPrimitive.Title>
      <DialogPrimitive.Description className="sr-only">Type to find a page or a migration. Use the arrow keys to choose and Enter to open.</DialogPrimitive.Description>
      <div className="flex items-center gap-3 border-b border-border px-4">
        <Search className="h-5 w-5 shrink-0 text-foreground-muted" aria-hidden="true" />
        <input
          ref={inputRef}
          autoFocus
          role="combobox"
          aria-expanded="true"
          aria-controls={listId}
          aria-activedescendant={visible[active] ? `${listId}-${active}` : undefined}
          aria-label="Search pages and migrations"
          autoComplete="off"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setPicked(0);
          }}
          onKeyDown={onKeyDown}
          placeholder="Search pages and migrations"
          className="h-14 flex-1 bg-transparent text-base text-foreground placeholder:text-foreground-subtle focus:outline-none"
        />
      </div>
      <ul id={listId} role="listbox" aria-label="Results" className="max-h-80 overflow-y-auto p-2">
        {visible.length === 0 ? (
          <li role="presentation" className="px-3 py-6 text-center text-sm text-foreground-muted">
            {jobs.isFetching ? "Searching" : "Nothing matches that"}
          </li>
        ) : (
          visible.map((c, i) => {
            const Icon = c.icon;
            return (
              <li
                key={c.id}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={i === active}
                onMouseMove={() => setPicked(i)}
                onClick={() => run(c)}
                className={cn("flex cursor-pointer items-center gap-3 rounded-md px-3 py-2.5 text-sm", i === active ? "bg-primary-soft text-primary-ink" : "text-foreground")}
              >
                <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                <span className="flex-1 truncate">{c.label}</span>
                {c.hint ? <span className="text-xs text-foreground-muted">{c.hint}</span> : null}
                {i === active ? <CornerDownLeft className="h-3.5 w-3.5" aria-hidden="true" /> : null}
              </li>
            );
          })
        )}
      </ul>
    </>
  );
}

/** The global command palette: Ctrl K (or Command K) opens it, typing narrows the list, the arrow keys move and Enter runs. */
export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-navy/60 data-[state=open]:animate-fade-in" />
        <DialogPrimitive.Content
          className="fixed left-1/2 top-[12vh] z-50 w-[calc(100vw-2rem)] max-w-xl -translate-x-1/2 overflow-hidden rounded-lg border border-border bg-surface-raised shadow-lg"
          onOpenAutoFocus={(e) => e.preventDefault()}
        >
          <PaletteBody close={() => onOpenChange(false)} />
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
