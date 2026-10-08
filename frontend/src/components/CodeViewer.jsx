import React, { useState } from 'react';
import { Copy, Check } from 'lucide-react';

// Minimal syntax highlighting (no dependency): enough to read Python
// declarations and JSON, like Airflow's Code view.
const COLORS = {
  keyword: '#c678dd',
  string: '#98c379',
  comment: '#7f848e',
  number: '#d19a66',
  name: '#61afef',
  key: '#e06c75',
  constant: '#d19a66',
};

const PYTHON_TOKENS = new RegExp(
  [
    '(#.*$)', // comment
    '("(?:[^"\\\\]|\\\\.)*"|\'(?:[^\'\\\\]|\\\\.)*\')', // string
    '\\b(def|class|return|from|import|as|if|elif|else|for|in|while|with|try|except|raise|lambda|yield|pass|and|or|not|is|self)\\b', // keyword
    '\\b(None|True|False)\\b', // constant
    '\\b(\\d+(?:\\.\\d+)?)\\b', // number
    '\\b([A-Z][A-Za-z0-9_]*)\\b', // class name
  ].join('|'),
  'g'
);

const JSON_TOKENS = new RegExp(
  [
    '("(?:[^"\\\\]|\\\\.)*")(?=\\s*:)', // key
    '("(?:[^"\\\\]|\\\\.)*")', // string
    '\\b(true|false|null)\\b', // constant
    '(-?\\b\\d+(?:\\.\\d+)?)\\b', // number
  ].join('|'),
  'g'
);

const PYTHON_KINDS = ['comment', 'string', 'keyword', 'constant', 'number', 'name'];
const JSON_KINDS = ['key', 'string', 'constant', 'number'];

function highlight(line, language) {
  const regex = language === 'json' ? JSON_TOKENS : PYTHON_TOKENS;
  const kinds = language === 'json' ? JSON_KINDS : PYTHON_KINDS;
  const parts = [];
  let last = 0;
  regex.lastIndex = 0;
  let match;
  while ((match = regex.exec(line)) !== null) {
    if (match[0] === '') {
      regex.lastIndex += 1;
      continue;
    }
    if (match.index > last) parts.push(line.slice(last, match.index));
    const group = match.slice(1).findIndex((g) => g !== undefined);
    parts.push(
      <span key={match.index} style={{ color: COLORS[kinds[group]] }}>
        {match[0]}
      </span>
    );
    last = match.index + match[0].length;
  }
  if (last < line.length) parts.push(line.slice(last));
  return parts;
}

export default function CodeViewer({ code, language = 'python', startLine = 1, title }) {
  const [copied, setCopied] = useState(false);
  const lines = (code || '').replace(/\n$/, '').split('\n');

  const handleCopy = () => {
    navigator.clipboard.writeText(code || '');
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div
      style={{
        border: '1px solid var(--border-default)',
        borderRadius: 'var(--radius-md)',
        background: 'var(--bg-surface)',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '6px 12px',
          borderBottom: '1px solid var(--border-default)',
          fontFamily: 'var(--font-mono)',
          fontSize: '0.75rem',
          color: 'var(--text-muted)',
        }}
      >
        <span>{title}</span>
        <button className="btn btn-ghost" onClick={handleCopy} title="Copier le code">
          {copied ? <Check size={13} /> : <Copy size={13} />}
          <span>{copied ? 'Copié' : 'Copier'}</span>
        </button>
      </div>
      <pre
        style={{
          margin: 0,
          padding: '10px 0',
          overflowX: 'auto',
          maxHeight: '560px',
          fontFamily: 'var(--font-mono)',
          fontSize: '0.78rem',
          lineHeight: 1.55,
          color: 'var(--text-primary)',
        }}
      >
        {lines.map((line, i) => (
          <div key={i} style={{ display: 'flex' }}>
            <span
              style={{
                minWidth: '3.5em',
                paddingRight: '12px',
                textAlign: 'right',
                color: 'var(--text-dim)',
                userSelect: 'none',
              }}
            >
              {startLine + i}
            </span>
            <span style={{ whiteSpace: 'pre', paddingRight: '16px' }}>
              {highlight(line, language)}
            </span>
          </div>
        ))}
      </pre>
    </div>
  );
}
