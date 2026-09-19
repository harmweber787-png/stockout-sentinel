"use client";

import { useEffect, useMemo } from "react";

/** Stable object URL for a Blob, revoked when the blob changes or the component unmounts. */
export function useObjectUrl(blob: Blob | null): string | null {
  const url = useMemo(() => (blob ? URL.createObjectURL(blob) : null), [blob]);
  useEffect(() => {
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [url]);
  return url;
}
