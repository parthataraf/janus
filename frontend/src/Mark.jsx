// The Janus mark: a two-faced disc (one half filled, one outlined) — one engine
// looking two ways.
export default function Mark({ className = "mark" }) {
  return (
    <svg className={className} viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="M12 2 A10 10 0 0 0 12 22 Z" fill="currentColor" />
    </svg>
  );
}
