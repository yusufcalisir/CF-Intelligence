import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
  fallbackTitle?: string;
  onReset?: () => void;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('Uncaught error caught by ErrorBoundary:', error, errorInfo);
  }

  public handleReset = () => {
    this.setState({ hasError: false, error: null });
    if (this.props.onReset) {
      this.props.onReset();
    }
  };

  public render() {
    if (this.state.hasError) {
      return (
        <div className="min-h-[280px] w-full p-6 rounded-2xl bg-[#090a1f]/90 border border-rose-500/30 backdrop-blur-xl shadow-2xl flex flex-col items-center justify-center text-center space-y-4 my-4">
          <div className="w-12 h-12 rounded-2xl bg-rose-500/10 border border-rose-500/30 text-rose-400 flex items-center justify-center text-2xl shadow-[0_0_25px_rgba(244,63,94,0.2)]">
            ⚠️
          </div>

          <div className="max-w-md space-y-1.5">
            <h3 className="text-base font-bold text-white tracking-tight">
              {this.props.fallbackTitle || 'Component Rendering Error'}
            </h3>
            <p className="text-xs text-slate-400 leading-relaxed font-mono">
              {this.state.error?.message || 'An unexpected runtime error occurred while rendering this module.'}
            </p>
          </div>

          <button
            onClick={this.handleReset}
            className="px-4 py-2 rounded-xl text-xs font-semibold text-white bg-gradient-to-r from-rose-600 to-indigo-600 hover:from-rose-500 hover:to-indigo-500 transition-all cursor-pointer shadow-lg active:scale-95 border border-white/10"
          >
            🔄 Reload Component
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
