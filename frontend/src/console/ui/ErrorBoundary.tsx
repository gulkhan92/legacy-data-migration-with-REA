import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button } from "@/components/ui/button";

type Props = { children: ReactNode; /** Changing this value clears a caught error (the route, for a page boundary). */ resetKey?: string };
type State = { error: Error | null; key?: string | undefined };

/** Catches a failure while rendering so that one broken page does not take the whole console down. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, key: this.props.resetKey };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    return props.resetKey !== state.key ? { error: null, key: props.resetKey } : null;
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("The page failed to render", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" className="mx-auto max-w-xl rounded-lg border border-border bg-surface-raised p-8 text-center">
        <h2 className="text-xl font-semibold">Something went wrong on this page</h2>
        <p className="mt-2 text-foreground-muted">
          The rest of the console still works. Reloading the page usually fixes it; if it does not, the details are in the browser console.
        </p>
        <div className="mt-6 flex justify-center gap-3">
          <Button size="sm" onClick={() => this.setState({ error: null })}>
            Try again
          </Button>
          <Button size="sm" variant="outline" onClick={() => window.location.reload()}>
            Reload the page
          </Button>
        </div>
      </div>
    );
  }
}
