// One of the sprite's icons (the <symbol>s in next.html).
export function Icon({ name, className }: { name: string; className?: string }) {
  return (
    <svg className={className ? `ico ${className}` : "ico"} aria-hidden="true" focusable="false">
      <use href={`#i-${name}`} />
    </svg>
  );
}
