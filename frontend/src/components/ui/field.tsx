import { cloneElement, isValidElement, useId, type ReactElement, type ReactNode } from "react";
import { Label } from "./input";

export type ControlProps = { id: string; "aria-invalid"?: boolean; "aria-describedby"?: string; "aria-required"?: boolean };

type Props = {
  label: string;
  error?: string | undefined;
  hint?: ReactNode;
  required?: boolean;
  /** The control itself, or a function that receives the props to put on it (when the control sits inside a wrapper, such as a password field with a toggle). */
  children: ReactElement<Partial<ControlProps>> | ((props: ControlProps) => ReactNode);
};

/** A label, the control, a hint and an error, wired together for screen readers (id, aria-describedby, aria-invalid). */
export function Field({ label, error, hint, required, children }: Props) {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const described = [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined;
  const controlProps: ControlProps = { id, "aria-invalid": error ? true : undefined, "aria-describedby": described, "aria-required": required ? true : undefined } as ControlProps;
  return (
    <div>
      <Label htmlFor={id}>
        {label}
        {required ? <span className="ml-0.5 text-danger" aria-hidden="true">*</span> : null}
      </Label>
      {typeof children === "function" ? children(controlProps) : isValidElement(children) ? cloneElement(children, controlProps) : children}
      {hint ? (
        <p id={hintId} className="mt-1.5 text-sm text-foreground-muted">
          {hint}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} role="alert" className="mt-1.5 text-sm font-medium text-danger">
          {error}
        </p>
      ) : null}
    </div>
  );
}
