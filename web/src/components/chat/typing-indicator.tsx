/** Three bouncing dots, cloud-portal's typing indicator. */
export function TypingIndicator() {
  return (
    <div className="flex items-center gap-1 py-1" role="status" aria-label="Investigating">
      {[0, 150, 300].map((delay) => (
        <span
          key={delay}
          className="h-2 w-2 animate-typing-dot-bounce rounded-full bg-muted-foreground"
          style={{ animationDelay: `${delay}ms` }}
        />
      ))}
    </div>
  );
}
