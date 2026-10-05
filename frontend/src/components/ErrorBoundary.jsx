import React from 'react';
import { AlertCircle, RefreshCw } from 'lucide-react';

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error('ErrorBoundary caught an error:', error, errorInfo);
    this.setState({ errorInfo });
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          className="ide-window"
          style={{
            padding: '30px',
            margin: '20px auto',
            maxWidth: '700px',
            background: 'var(--bg-surface)',
            border: '1px solid var(--status-failed-border)',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              color: 'var(--status-failed)',
              marginBottom: '16px',
            }}
          >
            <AlertCircle size={24} />
            <h3 style={{ fontSize: '1rem', fontWeight: 600 }}>
              UI Rendering Error caught by ErrorBoundary
            </h3>
          </div>

          <div
            style={{
              background: '#070a0f',
              padding: '12px',
              borderRadius: 'var(--radius-sm)',
              fontFamily: 'var(--font-mono)',
              fontSize: '0.8125rem',
              color: '#f87171',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              marginBottom: '16px',
            }}
          >
            {this.state.error?.toString()}
          </div>

          <button
            className="btn btn-primary"
            onClick={() => {
              this.setState({ hasError: false, error: null });
              window.location.reload();
            }}
          >
            <RefreshCw size={14} /> Reload Studio
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
