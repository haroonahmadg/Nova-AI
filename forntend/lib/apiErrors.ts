function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function formatDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail.trim()) {
    return detail.trim();
  }

  if (!Array.isArray(detail)) {
    return fallback;
  }

  const messages = detail.flatMap((item): string[] => {
    if (!isRecord(item) || typeof item.msg !== "string") {
      return [];
    }

    const location = Array.isArray(item.loc)
      ? item.loc.filter(
          (part): part is string | number =>
            typeof part === "string" || typeof part === "number",
        )
      : [];
    const fieldPath = location
      .filter((part, index) => !(index === 0 && part === "body"))
      .join(".");

    return [fieldPath ? `${fieldPath}: ${item.msg}` : item.msg];
  });

  return messages.length > 0 ? messages.join("; ") : fallback;
}

export function formatApiErrorResponse(
  body: unknown,
  fallback: string,
): string {
  if (!isRecord(body)) {
    return fallback;
  }

  return formatDetail(body.detail ?? body.message, fallback);
}
