"use client";

import { useCallback, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { ApiError } from "@/lib/api/client";
import { safeNext } from "@/lib/safeNext";

export type FormStatus =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "error"; message: string };

/** Shared submit/error/redirect plumbing for the auth forms. */
export function useAuthForm(defaultDestination: string) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [status, setStatus] = useState<FormStatus>({ kind: "idle" });

  const run = useCallback(
    async (action: () => Promise<unknown>) => {
      setStatus({ kind: "submitting" });
      try {
        await action();
        router.push(safeNext(searchParams.get("next"), defaultDestination));
        router.refresh();
      } catch (error) {
        setStatus({
          kind: "error",
          message:
            error instanceof ApiError
              ? error.status === 429
                ? "Too many attempts - try again in a few minutes"
                : error.message
              : "Something went wrong",
        });
      }
    },
    [router, searchParams, defaultDestination],
  );

  return { status, run };
}
