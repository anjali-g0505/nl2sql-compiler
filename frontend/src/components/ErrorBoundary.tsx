import { Component, type ReactNode } from "react";

interface State {
  error: Error | null;
}

/** Contains a rendering crash to one answer card instead of blanking the whole page. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    console.error("Answer failed to render:", error);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="card error">
          <p className="error-headline">This answer couldn't be displayed.</p>
          <p className="muted small">{this.state.error.message}</p>
        </div>
      );
    }
    return this.props.children;
  }
}
