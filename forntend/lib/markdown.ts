import type { ReactNode } from "react";

export function markdownText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(markdownText).join("");
  if (node && typeof node === "object" && "props" in node) {
    return markdownText((node.props as { children?: ReactNode }).children);
  }
  return "";
}
