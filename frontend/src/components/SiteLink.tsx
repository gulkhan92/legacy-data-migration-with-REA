import { forwardRef, type AnchorHTMLAttributes } from "react";
import { Link } from "react-router-dom";

type Props = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> & { to: string; external?: boolean };

/** An internal route link, or a plain anchor for addresses served by something other than the single page app (the API documentation). */
export const SiteLink = forwardRef<HTMLAnchorElement, Props>(({ to, external, children, ...props }, ref) => {
  if (external || /^(https?:|mailto:)/.test(to)) {
    return (
      <a ref={ref} href={to} {...props}>
        {children}
      </a>
    );
  }
  return (
    <Link ref={ref} to={to} {...props}>
      {children}
    </Link>
  );
});
SiteLink.displayName = "SiteLink";
