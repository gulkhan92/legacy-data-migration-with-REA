import { Globe } from "lucide-react";
import { Logo } from "@/components/Logo";
import { SiteLink } from "@/components/SiteLink";
import { NewsletterForm } from "@/components/forms";
import { DEMO_NOTICE, footerColumns } from "@/site/content/nav";

export function Footer() {
  return (
    <footer className="bg-navy text-white" aria-label="Site footer">
      <div className="mx-auto w-full max-w-site px-4 py-14 md:px-8 md:py-16">
        <div className="grid gap-10 lg:grid-cols-12">
          <div className="lg:col-span-4">
            <Logo onDark />
            <p className="mt-4 max-w-sm text-white/80">Move decades of legacy data into modern systems, with proof that nothing was lost.</p>
            <div className="relative mt-6 max-w-sm">
              <NewsletterForm />
            </div>
          </div>
          <nav aria-label="Footer" className="grid grid-cols-2 gap-8 sm:grid-cols-3 lg:col-span-8 lg:grid-cols-5">
            {footerColumns.map((column) => (
              <div key={column.title}>
                <h2 className="mb-3 text-sm font-semibold uppercase tracking-wider text-white/70">{column.title}</h2>
                <ul className="space-y-2">
                  {column.links.map((link) => (
                    <li key={link.label}>
                      <SiteLink to={link.to} external={link.external} className="text-sm text-white/90 hover:text-white hover:underline">
                        {link.label}
                      </SiteLink>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </nav>
        </div>
        <div className="mt-12 border-t border-white/15 pt-6">
          <p className="max-w-4xl text-sm text-white/75">{DEMO_NOTICE}</p>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 text-sm text-white/70">
            <p>&copy; {new Date().getFullYear()} LegacyLift. Sample project.</p>
            <p className="inline-flex items-center gap-1.5">
              <Globe className="h-4 w-4" aria-hidden="true" />
              <span>English (United States) &middot; Global</span>
            </p>
          </div>
        </div>
      </div>
    </footer>
  );
}
