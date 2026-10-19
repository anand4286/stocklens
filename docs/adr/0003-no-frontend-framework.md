# ADR 0003 — No front-end framework, no CDN

**Status:** accepted

## Context
The UI is six read-only tabs over one JSON document. Security posture matters
more than component reuse.

## Decision
Vanilla ES modules and hand-written SVG charts, served from the same origin.
No build step, no npm dependencies at runtime, no third-party script origins.

## Consequences
- The Content-Security-Policy can be `script-src 'self'` with no `unsafe-inline` and `connect-src 'self'`; an injected script could not load code or exfiltrate data elsewhere.
- No front-end supply chain to audit.
- Every DOM node is built through one helper using `textContent`, so XSS-safety is a property of one function.
- Cost: charts are simpler than a charting library's; adding interactivity is manual work.
