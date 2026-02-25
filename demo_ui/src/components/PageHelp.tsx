import type { ReactNode } from "react";

interface PageHelpProps {
  children: ReactNode;
}

/** Short functional help under each demo screen header. */
export function PageHelp({ children }: PageHelpProps) {
  return <aside className="page-help">{children}</aside>;
}
