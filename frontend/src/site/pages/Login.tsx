import { zodResolver } from "@hookform/resolvers/zod";
import { Eye, EyeOff, Loader2, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { z } from "zod";
import { describeError } from "@/components/forms";
import { Logo } from "@/components/Logo";
import { SiteLink } from "@/components/SiteLink";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api";
import { homeFor, useAuth, useSessionCheck } from "@/lib/auth";
import { usePageMeta } from "@/lib/seo";

const schema = z.object({
  email: z.string().trim().min(1, "Enter your email address").email("Enter a valid email address"),
  password: z.string().min(1, "Enter your password"),
});
type Values = z.infer<typeof schema>;

/** Only same-site paths are accepted as a place to return to, so a crafted link cannot send a signed in person elsewhere. */
export function safeNext(value: string | null | undefined): string | null {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return null;
  return value;
}

function signInMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 401) return "The email or password is not correct. After five wrong attempts an account is locked for 15 minutes.";
  return describeError(error);
}

export default function Login() {
  usePageMeta({ title: "Sign in", description: "Sign in to the LegacyLift console.", path: "/login", noindex: true });
  useSessionCheck({ onlyWithHint: true });
  const { me, loading, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const next = safeNext(new URLSearchParams(location.search).get("next"));
  const [show, setShow] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [forgot, setForgot] = useState(false);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { email: "", password: "" } });

  if (!loading && me) return <Navigate to={next ?? homeFor(me.role)} replace />;

  async function onSubmit(values: Values) {
    setFailure(null);
    try {
      const signedIn = await login(values.email, values.password);
      navigate(next ?? homeFor(signedIn.role), { replace: true });
    } catch (error) {
      setFailure(signInMessage(error));
    }
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <aside className="relative hidden overflow-hidden bg-navy p-12 text-white lg:flex lg:flex-col lg:justify-between" aria-label="About LegacyLift">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(40rem_28rem_at_90%_0%,rgb(15_98_254/0.5),transparent),radial-gradient(34rem_22rem_at_0%_100%,rgb(0_163_163/0.3),transparent)]" aria-hidden="true" />
        <SiteLink to="/" className="relative w-fit rounded-md" aria-label="LegacyLift, home">
          <Logo onDark />
        </SiteLink>
        <div className="relative max-w-md">
          <h2 className="text-4xl font-bold text-white">Prove every row made it across.</h2>
          <p className="mt-4 text-lg text-white/85">Discovery, mapping, transformation, validation and reconciliation, with a person approving every decision and a way back for every load.</p>
          <p className="mt-8 flex items-center gap-2 text-sm text-white/80">
            <ShieldCheck className="h-5 w-5 text-teal" aria-hidden="true" />
            Uploads are encrypted and never executed.
          </p>
        </div>
        <p className="relative text-sm text-white/70">A demonstration project with sample data.</p>
      </aside>

      <main id="main" tabIndex={-1} className="flex items-center justify-center bg-background px-4 py-12 sm:px-8">
        <div className="w-full max-w-md">
          <SiteLink to="/" className="mb-10 inline-block rounded-md lg:hidden" aria-label="LegacyLift, home">
            <Logo />
          </SiteLink>
          <h1 className="text-3xl font-bold">Sign in</h1>
          <p className="mt-2 text-foreground-muted">Use the account your administrator created for you. Registration is closed.</p>

          <form onSubmit={handleSubmit(onSubmit)} noValidate className="mt-8 space-y-5" aria-label="Sign in">
            {failure ? (
              <p role="alert" className="rounded-md border border-danger/30 bg-danger-soft p-3 text-sm font-medium text-danger">
                {failure}
              </p>
            ) : null}
            <Field label="Email" required error={errors.email?.message}>
              <Input type="email" autoComplete="username" inputMode="email" autoFocus {...register("email")} />
            </Field>
            <Field label="Password" required error={errors.password?.message}>
              {(control) => (
                <div className="relative">
                  <Input type={show ? "text" : "password"} autoComplete="current-password" className="pr-12" {...control} {...register("password")} />
                  <button
                    type="button"
                    onClick={() => setShow((value) => !value)}
                    aria-label={show ? "Hide password" : "Show password"}
                    aria-pressed={show}
                    className="absolute right-1 top-1 flex h-9 w-10 items-center justify-center rounded-md text-foreground-muted hover:bg-surface-muted hover:text-foreground"
                  >
                    {show ? <EyeOff className="h-5 w-5" aria-hidden="true" /> : <Eye className="h-5 w-5" aria-hidden="true" />}
                  </button>
                </div>
              )}
            </Field>
            <Button type="submit" size="lg" className="w-full" disabled={isSubmitting}>
              {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
              {isSubmitting ? "Signing in" : "Sign in"}
            </Button>
          </form>

          <div className="mt-6 text-sm">
            <button type="button" className="font-semibold text-primary underline-offset-2 hover:underline" aria-expanded={forgot} aria-controls="forgot-note" onClick={() => setForgot((value) => !value)}>
              Forgot your password?
            </button>
            {forgot ? (
              <p id="forgot-note" className="mt-2 rounded-md bg-surface-muted p-3 text-foreground-muted">
                Password reset by email is not built in this demonstration. Ask the administrator of your organization to set a new password for you.
              </p>
            ) : null}
          </div>
          <p className="mt-10 text-sm text-foreground-muted">
            New here? <SiteLink to="/demo" className="font-semibold underline">Request a demo</SiteLink> or read about <SiteLink to="/security" className="font-semibold underline">security</SiteLink>.
          </p>
        </div>
      </main>
    </div>
  );
}
