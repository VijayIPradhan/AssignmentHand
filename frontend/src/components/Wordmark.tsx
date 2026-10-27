export function Wordmark({ large = false }: { large?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2">
      <svg
        viewBox="0 0 28 28"
        className={large ? "size-8" : "size-6"}
        aria-hidden
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
      >
        {/* a ruled sheet with a red margin line and a written stroke */}
        <rect x="3.5" y="2.5" width="21" height="23" rx="2" className="text-ink" strokeWidth="1.5" />
        <path d="M9 3v22" stroke="#b4372f" strokeWidth="1.2" />
        <path d="M5 9.5h18M5 14.5h18M5 19.5h18" stroke="#c9d6ec" strokeWidth="1" />
        <path
          d="M11 13.5c1.2-2.4 2.4-2.6 2.6-.8.2 1.6-1 2.2.2 1.6 1.2-.7 1.8-2.2 2.9-1.8 1 .4-.2 1.8.8 1.7 1-.1 1.7-1.3 2.5-1.6"
          className="text-ink"
          strokeWidth="1.4"
        />
      </svg>
      <span className={large ? "font-serif text-2xl text-ink" : "font-serif text-[1.2rem] text-ink"}>AssignmentHand</span>
    </span>
  );
}
