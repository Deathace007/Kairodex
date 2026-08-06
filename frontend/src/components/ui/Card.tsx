export function Card({
  title,
  children,
  className = "",
}: {
  title?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-lg border p-4 ${className}`}
      style={{ background: "var(--surface-1)", borderColor: "var(--border)" }}
    >
      {title && (
        <h3
          className="mb-3 text-sm font-medium tracking-wide uppercase"
          style={{ color: "var(--text-secondary)" }}
        >
          {title}
        </h3>
      )}
      {children}
    </div>
  );
}
