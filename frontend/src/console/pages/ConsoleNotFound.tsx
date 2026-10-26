import { Link } from "react-router-dom";
import { homeFor, useAuth } from "@/lib/auth";
import { PageHeader } from "../ui/page";

export default function ConsoleNotFound() {
  const { me } = useAuth();
  return (
    <div className="mx-auto max-w-lg py-16 text-center">
      <PageHeader title="This page does not exist" description="The address may be mistyped, or the page may have moved." />
      <Link to={me ? homeFor(me.role) : "/"} className="font-semibold text-primary underline">
        Go to your start page
      </Link>
    </div>
  );
}
