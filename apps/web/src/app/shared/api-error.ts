interface StructuredApiError {
  error?: { message?: string };
}

export function apiErrorMessage(error: unknown, fallback: string): string {
  const structured = error as StructuredApiError | undefined;
  return structured?.error?.message ?? fallback;
}
