"use client";

import { useId } from "react";
import { SelectField, Slider } from "./ui";

export interface StyleValues {
  handwriting_size_mm: number;
  stroke_weight: number;
  slant_offset_deg: number;
  char_spacing: number;
  word_spacing: number;
  variation: number;
  letter_style: LetterStyle;
  ink_color: string;
}

export type LetterStyle = "auto" | "joined" | "separate";

export const STYLE_DEFAULTS: StyleValues = {
  handwriting_size_mm: 2.8,
  stroke_weight: 1.0,
  slant_offset_deg: 0,
  char_spacing: 1.0,
  word_spacing: 1.0,
  variation: 0.5,
  letter_style: "auto",
  ink_color: "#1b2a6b",
};

const INKS = [
  { value: "#1b2a6b", name: "Blue-black" },
  { value: "#1f3fae", name: "Royal blue" },
  { value: "#1d1d1f", name: "Black" },
  { value: "#3d4652", name: "Pencil grey" },
];

/** Shared handwriting style sliders (ranges match the backend's RenderSettings limits).
 * ``autoLabel`` names the "auto" letter style: what it resolves to where the controls are used. */
export function StyleControls({
  value,
  onChange,
  autoLabel,
}: {
  value: StyleValues;
  onChange: (v: StyleValues) => void;
  autoLabel: string;
}) {
  const set = <K extends keyof StyleValues>(k: K, v: StyleValues[K]) => onChange({ ...value, [k]: v });
  const colorId = useId();
  return (
    <div className="grid gap-x-6 gap-y-4 sm:grid-cols-2">
      <Slider
        label="Letter size"
        min={1.2}
        max={8}
        step={0.1}
        value={value.handwriting_size_mm}
        onChange={(v) => set("handwriting_size_mm", v)}
        format={(v) => `${v.toFixed(1)} mm`}
        hint="Height of small letters such as a, e and o."
      />
      <Slider
        label="Stroke weight"
        min={0.6}
        max={2}
        step={0.05}
        value={value.stroke_weight}
        onChange={(v) => set("stroke_weight", v)}
        format={(v) => `${v.toFixed(2)}×`}
      />
      <Slider
        label="Extra slant"
        min={-20}
        max={20}
        step={0.5}
        value={value.slant_offset_deg}
        onChange={(v) => set("slant_offset_deg", v)}
        format={(v) => `${v > 0 ? "+" : ""}${v.toFixed(1)}°`}
        hint="Added to your measured slant."
      />
      <Slider
        label="Letter spacing"
        min={0.3}
        max={3}
        step={0.05}
        value={value.char_spacing}
        onChange={(v) => set("char_spacing", v)}
        format={(v) => `${v.toFixed(2)}×`}
      />
      <Slider
        label="Word spacing"
        min={0.4}
        max={3}
        step={0.05}
        value={value.word_spacing}
        onChange={(v) => set("word_spacing", v)}
        format={(v) => `${v.toFixed(2)}×`}
      />
      <Slider
        label="Natural variation"
        min={0}
        max={1}
        step={0.05}
        value={value.variation}
        onChange={(v) => set("variation", v)}
        format={(v) => `${Math.round(v * 100)}%`}
        hint="How much each letter wobbles in size, angle and baseline."
      />
      <SelectField
        label="Letters"
        value={value.letter_style}
        onChange={(e) => set("letter_style", e.target.value as LetterStyle)}
        hint="Joined connects the letters of each word with joining strokes, like running handwriting."
      >
        <option value="auto">{autoLabel}</option>
        <option value="joined">Joined (cursive)</option>
        <option value="separate">Separate letters (print)</option>
      </SelectField>
      <fieldset className="sm:col-span-2">
        <legend className="text-[13px] font-medium">Ink colour</legend>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {INKS.map((ink) => (
            <button
              key={ink.value}
              type="button"
              onClick={() => set("ink_color", ink.value)}
              aria-pressed={value.ink_color.toLowerCase() === ink.value}
              className="flex items-center gap-2 rounded-full border border-line-strong bg-sheet py-1 pl-1 pr-3 text-[13px] aria-pressed:border-ink aria-pressed:bg-ink-wash"
            >
              <span className="size-5 rounded-full border border-black/10" style={{ background: ink.value }} aria-hidden />
              {ink.name}
            </button>
          ))}
          <label htmlFor={colorId} className="ml-1 flex items-center gap-2 text-[13px] text-muted">
            Custom
            <input
              id={colorId}
              type="color"
              value={/^#[0-9a-f]{6}$/i.test(value.ink_color) ? value.ink_color : "#1b2a6b"}
              onChange={(e) => set("ink_color", e.target.value)}
              className="h-7 w-9 cursor-pointer rounded border border-line-strong bg-sheet p-0.5"
            />
          </label>
        </div>
      </fieldset>
    </div>
  );
}
