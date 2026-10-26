import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { UserPlus } from "lucide-react";
import { useState } from "react";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ApiError, type Role } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatDay } from "@/lib/format";
import { adminApi, type Member } from "../api";
import { Modal, Pagination, SearchInput, Select, useDebounced } from "../ui/controls";
import { PageHeader, Panel } from "../ui/page";
import { ROLE_DESCRIPTIONS, roleLabel } from "../ui/roles";
import { describeError, EmptyState, ErrorState, LoadingRows } from "../ui/states";
import { useToast } from "../ui/toast";

const ROLES: Role[] = ["admin", "analyst", "viewer"];
const PAGE = 20;

const schema = z.object({
  full_name: z.string().trim().min(1, "Enter the person's name").max(200),
  email: z.string().trim().email("Enter a valid email address"),
  role: z.enum(["admin", "analyst", "viewer"]),
  password: z.string().min(12, "Use at least 12 characters").max(256),
});
type Values = z.infer<typeof schema>;

function AddUser({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const toast = useToast();
  const client = useQueryClient();
  const form = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { full_name: "", email: "", role: "viewer", password: "" } });
  const add = useMutation({
    mutationFn: (v: Values) => adminApi.createUser(v),
    onSuccess: (m) => {
      toast.success(`${m.full_name} was added`, "Give them the first password in person; they should change it.");
      form.reset();
      onOpenChange(false);
      void client.invalidateQueries({ queryKey: ["team"] });
    },
    onError: (error) => {
      if (error instanceof ApiError && error.fields.length > 0) {
        for (const f of error.fields) if (f.field in schema.shape) form.setError(f.field as keyof Values, { message: f.message });
      } else {
        form.setError("root", { message: describeError(error) });
      }
    },
  });
  const errors = form.formState.errors;
  const chosenRole = useWatch({ control: form.control, name: "role" });
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Add a person"
      description="Invitations by email are not available yet. Add the person here and give them the first password yourself."
    >
      <form className="space-y-4" onSubmit={form.handleSubmit((v) => add.mutate(v))} noValidate>
        <Field label="Name" error={errors.full_name?.message} required>
          <Input autoComplete="off" {...form.register("full_name")} />
        </Field>
        <Field label="Work email" error={errors.email?.message} required>
          <Input type="email" autoComplete="off" {...form.register("email")} />
        </Field>
        <Field label="Role" error={errors.role?.message} hint={ROLE_DESCRIPTIONS[chosenRole]}>
          {(props) => (
            <Select {...props} className="w-full" {...form.register("role")}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {roleLabel(r)}
                </option>
              ))}
            </Select>
          )}
        </Field>
        <Field label="First password" error={errors.password?.message} hint="At least 12 characters, and not containing the person's name or email." required>
          <Input type="password" autoComplete="new-password" {...form.register("password")} />
        </Field>
        {errors.root?.message ? (
          <p role="alert" className="text-sm font-medium text-danger">
            {errors.root.message}
          </p>
        ) : null}
        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" disabled={add.isPending}>
            Add the person
          </Button>
        </div>
      </form>
    </Modal>
  );
}

export default function Team() {
  const { me } = useAuth();
  const toast = useToast();
  const client = useQueryClient();
  const [text, setText] = useState("");
  const [role, setRole] = useState("");
  const [offset, setOffset] = useState(0);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<Member | null>(null);
  const q = useDebounced(text, 300);
  const params = { q, role, limit: PAGE, offset };
  const team = useQuery({ queryKey: ["team", params], queryFn: ({ signal }) => adminApi.users(params, signal), placeholderData: (previous) => previous });

  const changeRole = useMutation({
    mutationFn: ({ member, next }: { member: Member; next: Role }) => adminApi.changeRole(member.user_id, next),
    onSuccess: (m) => {
      toast.success(`${m.full_name} is now ${roleLabel(m.role).toLowerCase()}`);
      void client.invalidateQueries({ queryKey: ["team"] });
    },
    onError: (error) => {
      toast.error("The role could not be changed", describeError(error));
      void client.invalidateQueries({ queryKey: ["team"] });
    },
  });
  const remove = useMutation({
    mutationFn: (m: Member) => adminApi.removeUser(m.user_id),
    onSuccess: () => {
      toast.success("The person was removed");
      setRemoving(null);
      void client.invalidateQueries({ queryKey: ["team"] });
    },
    onError: (error) => {
      setRemoving(null);
      toast.error("They could not be removed", describeError(error));
    },
  });

  return (
    <>
      <PageHeader
        title="Team"
        description="The people of this organization and what each may do."
        actions={
          <Button onClick={() => setAdding(true)}>
            <UserPlus className="h-4 w-4" aria-hidden="true" />
            Add a person
          </Button>
        }
      />
      <div className="space-y-6">
        <Panel title="Roles">
          <dl className="grid gap-4 md:grid-cols-3">
            {ROLES.map((r) => (
              <div key={r}>
                <dt className="font-semibold">{roleLabel(r)}</dt>
                <dd className="mt-1 text-sm text-foreground-muted">{ROLE_DESCRIPTIONS[r]}</dd>
              </div>
            ))}
          </dl>
        </Panel>
        <div className="flex flex-wrap items-end gap-3">
          <SearchInput
            value={text}
            onChange={(v) => {
              setText(v);
              setOffset(0);
            }}
            label="Search by name or email"
            className="w-72"
          />
          <Select
            value={role}
            aria-label="Role"
            onChange={(e) => {
              setRole(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">All roles</option>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {roleLabel(r)}
              </option>
            ))}
          </Select>
        </div>
        {team.isPending ? (
          <LoadingRows rows={4} />
        ) : team.isError ? (
          <ErrorState error={team.error} onRetry={() => void team.refetch()} />
        ) : team.data.items.length === 0 ? (
          <EmptyState title="Nobody matches">Try another name or role.</EmptyState>
        ) : (
          <>
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="w-full min-w-[44rem] text-left text-sm">
                <caption className="sr-only">People in this organization</caption>
                <thead className="bg-surface-muted text-foreground-muted">
                  <tr>
                    <th scope="col" className="px-4 py-3 font-semibold">
                      Person
                    </th>
                    <th scope="col" className="px-4 py-3 font-semibold">
                      Role
                    </th>
                    <th scope="col" className="px-4 py-3 font-semibold">
                      Member since
                    </th>
                    <th scope="col" className="px-4 py-3">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {team.data.items.map((m) => {
                    const self = m.user_id === me?.user_id;
                    return (
                      <tr key={m.user_id} className="border-t border-border">
                        <td className="px-4 py-3">
                          <span className="font-semibold">{m.full_name}</span> {self ? <Badge variant="primary">You</Badge> : null}
                          {!m.is_active ? (
                            <Badge variant="neutral" className="ml-1">
                              Inactive
                            </Badge>
                          ) : null}
                          <span className="block text-foreground-muted">{m.email}</span>
                        </td>
                        <td className="px-4 py-3">
                          <Select
                            value={m.role}
                            disabled={self || changeRole.isPending}
                            aria-label={`Role of ${m.full_name}`}
                            onChange={(e) => changeRole.mutate({ member: m, next: e.target.value as Role })}
                          >
                            {ROLES.map((r) => (
                              <option key={r} value={r}>
                                {roleLabel(r)}
                              </option>
                            ))}
                          </Select>
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 text-foreground-muted">{formatDay(m.member_since)}</td>
                        <td className="px-4 py-3 text-right">
                          <Button variant="ghost" size="sm" disabled={self} onClick={() => setRemoving(m)} aria-label={`Remove ${m.full_name}`}>
                            Remove
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <Pagination total={team.data.total} limit={PAGE} offset={offset} onChange={setOffset} noun="people" />
          </>
        )}
      </div>
      <AddUser open={adding} onOpenChange={setAdding} />
      <Modal
        open={!!removing}
        onOpenChange={(open) => !open && setRemoving(null)}
        title={`Remove ${removing?.full_name ?? ""}?`}
        description="They lose access to this organization at once. Their past work stays in the audit log."
        footer={
          <>
            <Button variant="outline" onClick={() => setRemoving(null)}>
              Keep them
            </Button>
            <Button variant="secondary" disabled={remove.isPending} onClick={() => removing && remove.mutate(removing)}>
              Remove them
            </Button>
          </>
        }
      />
    </>
  );
}
