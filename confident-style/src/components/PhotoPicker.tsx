"use client";

import { useId } from "react";
import { useObjectUrl } from "@/lib/object-url";

interface Props {
  label: string;
  hint: string;
  value: Blob | null;
  onChange: (file: File | null) => void;
  disabled?: boolean;
}

export function PhotoPicker({ label, hint, value, onChange, disabled }: Props) {
  const id = useId();
  const preview = useObjectUrl(value);

  return (
    <div className="rounded-3xl bg-white p-4 shadow-soft ring-1 ring-line">
      <div className="mb-3 flex items-baseline justify-between">
        <label htmlFor={id} className="font-medium text-ink">
          {label}
        </label>
        {value && (
          <button
            type="button"
            className="text-sm text-accent-deep underline-offset-2 hover:underline"
            onClick={() => onChange(null)}
            disabled={disabled}
          >
            Change
          </button>
        )}
      </div>
      <label
        htmlFor={id}
        className={`relative flex aspect-[3/4] cursor-pointer items-center justify-center overflow-hidden rounded-2xl border-2 border-dashed transition ${
          preview ? "border-transparent" : "border-line bg-blush/60 hover:bg-blush"
        } ${disabled ? "pointer-events-none opacity-60" : ""}`}
      >
        {preview ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={preview} alt={`${label} preview`} className="h-full w-full object-cover" />
        ) : (
          <span className="px-6 text-center text-sm text-muted">
            <span className="mb-2 block text-3xl" aria-hidden>
              ＋
            </span>
            Tap to take or choose a photo
          </span>
        )}
      </label>
      <input
        id={id}
        type="file"
        accept="image/*"
        className="sr-only"
        disabled={disabled}
        onChange={(e) => onChange(e.target.files?.[0] ?? null)}
      />
      <p className="mt-3 text-sm leading-relaxed text-muted">{hint}</p>
    </div>
  );
}
