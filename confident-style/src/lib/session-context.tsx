"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import type { StyleAnalysis } from "./schema";
import { useSessionId, useStoredAnalysis, writeAnalysis } from "./session-store";

/**
 * Holds the two photos (in-memory Blobs, browser only) and the analysis for the
 * current visit. Photos are never persisted anywhere.
 */
export interface Photos {
  body: Blob | null;
  face: Blob | null;
}

interface StyleSession {
  photos: Photos;
  /** Functional update per slot so two quick selections never overwrite each other. */
  setPhoto: (slot: keyof Photos, blob: Blob | null) => void;
  analysis: StyleAnalysis | null;
  setAnalysis: (analysis: StyleAnalysis | null) => void;
  /** Random per-visit id used only for anonymous feedback. */
  sessionId: string;
  reset: () => void;
}

const StyleSessionContext = createContext<StyleSession | null>(null);

export function StyleSessionProvider({ children }: { children: ReactNode }) {
  const [photos, setPhotos] = useState<Photos>({ body: null, face: null });
  const analysis = useStoredAnalysis();
  const sessionId = useSessionId();

  const setPhoto = useCallback((slot: keyof Photos, blob: Blob | null) => {
    setPhotos((prev) => ({ ...prev, [slot]: blob }));
  }, []);
  const setAnalysis = useCallback((next: StyleAnalysis | null) => writeAnalysis(next), []);
  const reset = useCallback(() => {
    setPhotos({ body: null, face: null });
    writeAnalysis(null);
  }, []);

  const value = useMemo<StyleSession>(
    () => ({ photos, setPhoto, analysis, setAnalysis, sessionId, reset }),
    [photos, setPhoto, analysis, setAnalysis, sessionId, reset],
  );

  return <StyleSessionContext.Provider value={value}>{children}</StyleSessionContext.Provider>;
}

export function useStyleSession(): StyleSession {
  const ctx = useContext(StyleSessionContext);
  if (!ctx) throw new Error("useStyleSession must be used inside StyleSessionProvider");
  return ctx;
}
