import type { ReactNode } from 'react';

interface CardProps {
  title?: ReactNode;
  hint?: ReactNode;
  children: ReactNode;
}

export function Card({ title, hint, children }: CardProps) {
  return (
    <div className="card">
      {title !== undefined && title !== null ? (
        <h3>
          {title}
          {hint ? <span className="hint">{hint}</span> : null}
        </h3>
      ) : null}
      {children}
    </div>
  );
}
