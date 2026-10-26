import { zodResolver } from "@hookform/resolvers/zod";
import { CheckCircle2, Loader2 } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input, Textarea } from "@/components/ui/input";
import { ApiError, publicApi, type LeadKind } from "@/lib/api";

export const emailSchema = z.string().trim().min(1, "Enter your email address").max(320, "That address is too long").email("Enter a valid email address");

const leadSchema = z.object({
  name: z.string().trim().max(200, "Keep the name under 200 characters").optional(),
  email: emailSchema,
  company: z.string().trim().max(200, "Keep the company under 200 characters").optional(),
  message: z.string().trim().max(4000, "Keep the message under 4,000 characters").optional(),
  website: z.string().optional(), // the hidden field: people leave it empty
});
export type LeadValues = z.infer<typeof leadSchema>;

/** Turns a failed request into something a person can act on. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) return "Too many requests. Please wait a minute and try again.";
    if (error.status === 0) return error.message;
    if (error.status >= 500) return "Something went wrong on our side. Please try again in a moment.";
    return error.message;
  }
  return "Something went wrong. Please try again.";
}

function HoneypotField({ register }: { register: ReturnType<typeof useForm<LeadValues>>["register"] }) {
  // Hidden from people and from assistive technology; a bot that fills every field gives itself away.
  return (
    <div aria-hidden="true" className="absolute -left-[9999px] h-0 w-0 overflow-hidden">
      <label>
        Website
        <input type="text" tabIndex={-1} autoComplete="off" {...register("website")} />
      </label>
    </div>
  );
}

export function LeadForm({ kind, submitLabel, messageLabel, messageHint, requireMessage = false }: { kind: Exclude<LeadKind, "newsletter">; submitLabel: string; messageLabel: string; messageHint?: string; requireMessage?: boolean }) {
  const [done, setDone] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const schema = requireMessage ? leadSchema.extend({ message: z.string().trim().min(10, "Tell us a little more (at least 10 characters)").max(4000, "Keep the message under 4,000 characters") }) : leadSchema;
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<LeadValues>({ resolver: zodResolver(schema), defaultValues: { name: "", email: "", company: "", message: "", website: "" } });

  async function onSubmit(values: LeadValues) {
    setFailure(null);
    try {
      await publicApi.lead({ kind, email: values.email, ...(values.name ? { name: values.name } : {}), ...(values.company ? { company: values.company } : {}), ...(values.message ? { message: values.message } : {}), ...(values.website ? { website: values.website } : {}) });
      setDone(true);
      reset();
    } catch (error) {
      setFailure(describeError(error));
    }
  }

  if (done) {
    return (
      <div role="status" className="rounded-lg border border-success/30 bg-success-soft p-6">
        <p className="flex items-center gap-2 text-lg font-semibold text-success">
          <CheckCircle2 className="h-5 w-5" aria-hidden="true" />
          Thank you, we have your message
        </p>
        <p className="mt-2 text-foreground-muted">This is a demonstration site: your message is stored for the administrator of the demo to read, and nobody will be sold anything.</p>
        <Button variant="outline" className="mt-4" onClick={() => setDone(false)}>
          Send another message
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)} noValidate className="relative space-y-5" aria-label={kind === "demo" ? "Request a demo" : "Contact form"}>
      <div className="grid gap-5 sm:grid-cols-2">
        <Field label="Name" error={errors.name?.message}>
          <Input autoComplete="name" {...register("name")} />
        </Field>
        <Field label="Work email" required error={errors.email?.message}>
          <Input type="email" autoComplete="email" inputMode="email" {...register("email")} />
        </Field>
      </div>
      <Field label="Company" error={errors.company?.message}>
        <Input autoComplete="organization" {...register("company")} />
      </Field>
      <Field label={messageLabel} required={requireMessage} hint={messageHint} error={errors.message?.message}>
        <Textarea rows={5} {...register("message")} />
      </Field>
      <HoneypotField register={register} />
      {failure ? (
        <p role="alert" className="rounded-md border border-danger/30 bg-danger-soft p-3 text-sm font-medium text-danger">
          {failure}
        </p>
      ) : null}
      <Button type="submit" size="lg" disabled={isSubmitting}>
        {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
        {isSubmitting ? "Sending" : submitLabel}
      </Button>
    </form>
  );
}

const newsletterSchema = z.object({ email: emailSchema, website: z.string().optional() });
type NewsletterValues = z.infer<typeof newsletterSchema>;

/** The one field in the footer. It stores a lead of the kind "newsletter". */
export function NewsletterForm() {
  const [state, setState] = useState<"idle" | "done" | "error">("idle");
  const [message, setMessage] = useState("");
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<NewsletterValues>({ resolver: zodResolver(newsletterSchema), defaultValues: { email: "", website: "" } });

  async function onSubmit(values: NewsletterValues) {
    setState("idle");
    try {
      await publicApi.lead({ kind: "newsletter", email: values.email, ...(values.website ? { website: values.website } : {}) });
      setState("done");
      reset();
    } catch (error) {
      setMessage(describeError(error));
      setState("error");
    }
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)} noValidate aria-label="Newsletter">
      <label htmlFor="newsletter-email" className="mb-2 block text-sm font-semibold text-white">
        Stay in touch
      </label>
      <div className="flex gap-2">
        <input
          id="newsletter-email"
          type="email"
          autoComplete="email"
          placeholder="you@company.com"
          aria-invalid={errors.email ? true : undefined}
          aria-describedby="newsletter-status"
          className="h-11 min-w-0 flex-1 rounded-md border border-white/30 bg-white/10 px-3 text-white placeholder:text-white/60 hover:border-white/50 aria-[invalid=true]:border-red-300"
          {...register("email")}
        />
        <div aria-hidden="true" className="absolute -left-[9999px] h-0 w-0 overflow-hidden">
          <input type="text" tabIndex={-1} autoComplete="off" {...register("website")} />
        </div>
        <Button type="submit" variant="onDark" disabled={isSubmitting}>
          {isSubmitting ? "Sending" : "Subscribe"}
        </Button>
      </div>
      <p id="newsletter-status" role={state === "idle" && !errors.email ? undefined : "status"} className="mt-2 min-h-5 text-sm text-white/80">
        {errors.email?.message ?? (state === "done" ? "Thank you, we will be in touch." : state === "error" ? message : "Occasional notes about the project. No spam.")}
      </p>
    </form>
  );
}
