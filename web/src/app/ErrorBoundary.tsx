import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button } from "../components/ui";
export class AppErrorBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(_error: Error, _info: ErrorInfo) {}
  render() {
    return this.state.failed ? (
      <main className="standalone" role="alert">
        <h1>This screen was interrupted</h1>
        <p>
          Reload Atlas to recover the screen. Questions are never automatically
          resubmitted; saved conversations remain available after signing in.
        </p>
        <Button onClick={() => window.location.reload()}>Reload Atlas</Button>
      </main>
    ) : (
      this.props.children
    );
  }
}
