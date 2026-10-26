import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, Loader2 } from "lucide-react";
import { PageHero, Section } from "@/components/layout";
import { publicApi } from "@/lib/api";
import { usePageMeta } from "@/lib/seo";

/** What can honestly be reported: whether the API answers its health check. It does not claim uptime figures it does not measure. */
export default function Status() {
  usePageMeta({ title: "Status", description: "Whether the LegacyLift API is answering right now.", path: "/status" });
  const { data, isPending, isError, dataUpdatedAt, errorUpdatedAt, refetch, isFetching } = useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => publicApi.health(signal),
    refetchInterval: 30_000,
    retry: false,
  });
  const ok = data?.status === "ok";
  const checked = new Date(Math.max(dataUpdatedAt, errorUpdatedAt));
  return (
    <>
      <PageHero eyebrow="Status" title="System status" lede="A live check of the API. Nothing else is measured here." />
      <Section>
        <div className="max-w-2xl rounded-xl border border-border bg-surface-raised p-6 shadow-sm" aria-live="polite">
          <div className="flex items-center gap-3">
            {isPending ? (
              <Loader2 className="h-7 w-7 animate-spin text-foreground-muted" aria-hidden="true" />
            ) : ok ? (
              <CheckCircle2 className="h-7 w-7 text-success" aria-hidden="true" />
            ) : (
              <CircleAlert className="h-7 w-7 text-danger" aria-hidden="true" />
            )}
            <div>
              <p className="text-xl font-semibold">{isPending ? "Checking" : ok ? "The API is answering" : "The API is not answering"}</p>
              {!isPending ? <p className="text-sm text-foreground-muted">Checked at {checked.toLocaleTimeString("en-US")}. The page checks again every 30 seconds.</p> : null}
            </div>
          </div>
          {isError ? <p className="mt-4 text-foreground-muted">If you are running the project locally, start the API (see the project documentation) and check again.</p> : null}
          <button type="button" onClick={() => void refetch()} disabled={isFetching} className="mt-5 rounded-md border border-border-strong px-4 py-2 text-sm font-semibold hover:bg-surface-muted disabled:opacity-60">
            Check now
          </button>
        </div>
      </Section>
    </>
  );
}
