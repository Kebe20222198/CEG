import { useEffect, useState } from 'react';

// Minimal hash router (no dependency): every view of the Studio has its own
// URL — e.g. #/workflows/tri_tickets_support/grid — so it can be bookmarked,
// shared, and reached with the browser's back and forward buttons.

function currentPath() {
  const hash = window.location.hash.replace(/^#/, '');
  return hash.startsWith('/') ? hash : '/workflows';
}

export function navigate(path) {
  window.location.hash = path;
}

export function useRoute() {
  const [path, setPath] = useState(currentPath);

  useEffect(() => {
    const onChange = () => setPath(currentPath());
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);

  const segments = path.split('/').filter(Boolean).map(decodeURIComponent);
  return { path, segments };
}

export function href(path) {
  return `#${path}`;
}
