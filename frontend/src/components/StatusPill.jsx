import React from 'react';
import { statusLabel } from '../status';

// A status badge with its French label (colours come from theme.css).
export default function StatusPill({ status }) {
  return (
    <span className={`status-pill ${status}`} title={status}>
      <span className="status-pill-dot" />
      <span>{statusLabel(status)}</span>
    </span>
  );
}
