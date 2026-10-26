import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileUp, Server } from "lucide-react";
import { useId, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/input";
import { cn } from "@/lib/cn";
import { formatBytes } from "@/lib/format";
import { jobsApi, pipelineApi, sourcesApi, type Job } from "../api";
import { Select } from "../ui/controls";
import { describeError, ErrorState, LoadingRows } from "../ui/states";
import { StepFrame, StepNote } from "./shared";

type Mode = "sample" | "upload";

export function SourceStep({ job, working, onCreated, onNext }: { job: Job | null; working: boolean; onCreated: (jobId: string) => void; onNext?: () => void }) {
  const [params] = useSearchParams();
  const client = useQueryClient();
  const [mode, setMode] = useState<Mode>("sample");
  const [sample, setSample] = useState<string>(params.get("sample") ?? "");
  const [chosenSchema, setSchema] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dataFile, setDataFile] = useState<File | null>(null);
  const fileId = useId();
  const dataId = useId();
  const sampleLegend = useId();

  const samples = useQuery({ queryKey: ["samples"], queryFn: ({ signal }) => sourcesApi.samples(signal), staleTime: 5 * 60_000 });
  const schemas = useQuery({ queryKey: ["target-schemas"], queryFn: ({ signal }) => pipelineApi.schemas(signal), staleTime: 10 * 60_000 });

  // A sample brings its own target schema, which the person can still change.
  const schema = chosenSchema ?? samples.data?.find((s) => s.name === sample)?.target_schema ?? "";

  const source = useQuery({ queryKey: ["source", job?.source_id], queryFn: ({ signal }) => sourcesApi.get(job?.source_id as string, signal), enabled: !!job });

  const create = useMutation({
    mutationFn: async () => {
      let sourceId: string;
      let dataSourceId: string | null = null;
      let target = schema;
      if (mode === "sample") {
        const registered = await sourcesApi.registerSample(sample);
        sourceId = registered.source_id;
        dataSourceId = registered.data_source_id;
        target = schema || registered.target_schema;
      } else {
        if (!file) throw new Error("Choose a file first");
        sourceId = (await sourcesApi.upload(file)).id;
        if (dataFile) dataSourceId = (await sourcesApi.upload(dataFile)).id;
      }
      return jobsApi.create({ source_id: sourceId, data_source_id: dataSourceId, target_schema: target });
    },
    onSuccess: (created) => {
      void client.invalidateQueries({ queryKey: ["jobs"] });
      void client.invalidateQueries({ queryKey: ["overview"] });
      onCreated(created.id);
    },
  });

  if (job) {
    const schemaName = schemas.data?.find((s) => s.id === job.target_schema_id)?.name;
    return (
      <StepFrame
        title="Source"
        intro="The system this migration brings in. It was chosen when the migration was created and cannot change; start a new migration for another system."
        footer={
          onNext ? (
            <Button className="ml-auto" onClick={onNext}>
              Continue to discovery
            </Button>
          ) : null
        }
      >
        <dl className="grid gap-4 rounded-lg border border-border bg-surface p-5 sm:grid-cols-2 lg:grid-cols-4">
          <Fact label="System" value={job.source_name ?? "unknown"} />
          <Fact label="Kind" value={String(job.source_kind ?? "")} />
          <Fact label="Target schema" value={schemaName ?? "not chosen"} />
          <Fact label="Created by" value={job.owner_name ?? "unknown"} />
        </dl>
        {source.data ? (
          <p className="text-sm text-foreground-muted">
            {formatBytes(source.data.size_bytes)} {source.data.is_sample ? "(a bundled sample system)" : "(uploaded, stored encrypted)"}
          </p>
        ) : null}
      </StepFrame>
    );
  }

  if (!working) {
    return <StepNote tone="warning">Your role can look at migrations but not start one. Ask an administrator to make you an analyst.</StepNote>;
  }

  const ready = mode === "sample" ? !!sample && !!schema : !!file && !!schema;
  return (
    <StepFrame
      title="Source"
      intro="Pick one of the bundled legacy systems, or upload your own. An uploaded file is only ever read as data: it is stored encrypted and never run."
      footer={
        <Button className="ml-auto" disabled={!ready || create.isPending} onClick={() => create.mutate()}>
          {create.isPending ? "Creating the migration" : "Create the migration"}
        </Button>
      }
    >
      <div role="radiogroup" aria-label="Where the system comes from" className="inline-flex rounded-lg bg-surface-muted p-1">
        {(
          [
            ["sample", "A sample system", Server],
            ["upload", "Upload a file", FileUp],
          ] as const
        ).map(([value, label, Icon]) => (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={mode === value}
            onClick={() => setMode(value)}
            className={cn(
              "inline-flex items-center gap-2 rounded-md px-4 py-2 text-sm font-semibold",
              mode === value ? "bg-background text-primary shadow-sm" : "text-foreground-muted hover:text-foreground",
            )}
          >
            <Icon className="h-4 w-4" aria-hidden="true" />
            {label}
          </button>
        ))}
      </div>

      {mode === "sample" ? (
        samples.isPending ? (
          <LoadingRows rows={2} />
        ) : samples.isError ? (
          <ErrorState error={samples.error} onRetry={() => void samples.refetch()} />
        ) : (
          <fieldset>
            <legend id={sampleLegend} className="mb-2 text-sm font-medium">
              Sample systems
            </legend>
            <div className="grid gap-4 md:grid-cols-2">
              {samples.data.map((s) => (
                <label
                  key={s.name}
                  className={cn(
                    "flex cursor-pointer flex-col rounded-lg border p-4",
                    sample === s.name ? "border-primary bg-primary-soft" : "border-border bg-surface hover:border-border-strong",
                    !s.available && "cursor-not-allowed opacity-60",
                  )}
                >
                  <span className="flex items-center gap-2">
                    <input
                      type="radio"
                      name="sample"
                      value={s.name}
                      checked={sample === s.name}
                      disabled={!s.available}
                      onChange={() => setSample(s.name)}
                      className="h-4 w-4 accent-primary"
                    />
                    <span className="font-semibold">{s.title}</span>
                  </span>
                  <span className="mt-1 text-sm text-foreground-muted">{s.description}</span>
                  <span className="mt-2 text-xs text-foreground-muted">
                    Files: {s.files.join(", ")}. Target schema: {s.target_schema}.
                  </span>
                  {!s.available ? <span className="mt-2 text-xs text-danger">The sample files are not installed on this server.</span> : null}
                </label>
              ))}
            </div>
          </fieldset>
        )
      ) : (
        <div className="grid gap-5 md:grid-cols-2">
          <div>
            <Label htmlFor={fileId}>The system file</Label>
            <input
              id={fileId}
              type="file"
              accept=".sqlite,.sqlite3,.db,.csv,.tsv,.txt,.dll,.exe,.cdb,.dat,.bin"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="block w-full text-sm file:mr-3 file:rounded-md file:border file:border-border-strong file:bg-surface-muted file:px-3 file:py-2 file:font-medium"
            />
            <p className="mt-1.5 text-sm text-foreground-muted">A SQLite database, a delimited text file, or the program (.dll or .exe) of a .NET application.</p>
          </div>
          <div>
            <Label htmlFor={dataId}>The data file (only for a .NET application)</Label>
            <input
              id={dataId}
              type="file"
              accept=".cdb,.dat,.bin"
              onChange={(e) => setDataFile(e.target.files?.[0] ?? null)}
              className="block w-full text-sm file:mr-3 file:rounded-md file:border file:border-border-strong file:bg-surface-muted file:px-3 file:py-2 file:font-medium"
            />
            <p className="mt-1.5 text-sm text-foreground-muted">The file the program writes its records to. Leave it empty for a database.</p>
          </div>
        </div>
      )}

      <div className="max-w-sm">
        <Label htmlFor="target-schema">Target schema</Label>
        <Select id="target-schema" value={schema} onChange={(e) => setSchema(e.target.value)} className="w-full">
          <option value="">Choose the schema to map to</option>
          {(schemas.data ?? []).map((s) => (
            <option key={s.id} value={s.name}>
              {s.name} (version {s.version})
            </option>
          ))}
        </Select>
      </div>

      {create.isError ? <StepNote tone="danger">{describeError(create.error)}</StepNote> : null}
    </StepFrame>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-sm text-foreground-muted">{label}</dt>
      <dd className="font-semibold">{value}</dd>
    </div>
  );
}
