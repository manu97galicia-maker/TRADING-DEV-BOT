import type { ReactNode } from "react";

export function Badge({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-full border border-eden-200 bg-white px-3 py-1 text-xs font-medium text-eden-700">
      {children}
    </span>
  );
}
