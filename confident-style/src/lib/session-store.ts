"use client";

import { useSyncExternalStore } from "react";
import type { StyleAnalysis } from "./schema";

/**
 * Tiny external store backed by sessionStorage so the analysis survives a refresh
 * on the results screen. The server snapshot is always empty, which keeps
 * hydration deterministic. Photos are deliberately NOT stored here.
 */
const ANALYSIS_KEY = "confident-style:analysis";
const SESSION_KEY = "confident-style:session";

type Listener = () => void;
const listeners = new Set<Listener>();
let cachedAnalysis: StyleAnalysis | null | undefined;
let cachedSessionId: string | undefined;

function emit() {
  for (const l of listeners) l();
}

function subscribe(listener: Listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

function readAnalysis(): StyleAnalysis | null {
  if (cachedAnalysis !== undefined) return cachedAnalysis;
  try {
    const raw = sessionStorage.getItem(ANALYSIS_KEY);
    cachedAnalysis = raw ? (JSON.parse(raw) as StyleAnalysis) : null;
  } catch {
    cachedAnalysis = null;
  }
  return cachedAnalysis;
}

function readSessionId(): string {
  if (cachedSessionId !== undefined) return cachedSessionId;
  try {
    let id = sessionStorage.getItem(SESSION_KEY);
    if (!id) {
      id = newId();
      sessionStorage.setItem(SESSION_KEY, id);
    }
    cachedSessionId = id;
  } catch {
    cachedSessionId = newId();
  }
  return cachedSessionId;
}

export function writeAnalysis(next: StyleAnalysis | null) {
  cachedAnalysis = next;
  try {
    if (next) sessionStorage.setItem(ANALYSIS_KEY, JSON.stringify(next));
    else sessionStorage.removeItem(ANALYSIS_KEY);
  } catch {
    /* private mode etc. - the in-memory cache still works for this visit */
  }
  emit();
}

export function useStoredAnalysis(): StyleAnalysis | null {
  return useSyncExternalStore(subscribe, readAnalysis, () => null);
}

export function useSessionId(): string {
  return useSyncExternalStore(subscribe, readSessionId, () => "");
}

/** False during SSR and the hydration render, true afterwards. */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
}
