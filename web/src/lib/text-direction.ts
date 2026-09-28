/**
 * Which way a certificate value reads.
 *
 * Many fields on a Pakistani certificate are in Urdu, and an Urdu name in a cell
 * that lays out left-to-right renders with its punctuation and any Latin digits in
 * the wrong places. The direction is therefore decided per value rather than per
 * column - one column routinely holds both scripts.
 *
 * The rule is Unicode's first-strong heuristic: the first character with a strong
 * direction decides, and neutrals (digits, spaces, dashes, slashes) are skipped.
 * A date or a CNIC is all neutral and stays left-to-right, which is what a clerk
 * expects to see.
 */

export type TextDirection = "ltr" | "rtl";

// Arabic, Arabic Supplement/Extended, Hebrew, and the Arabic presentation forms
// Urdu text pasted from older systems still arrives in.
const STRONG_RTL =
  /[֐-׿؀-ۿ܀-ݏݐ-ݿހ-޿ࠀ-࠿ࢠ-ࣿיִ-﷿ﹰ-﻿]/;

// Latin, Greek, Cyrillic and the Latin extensions - deliberately not digits.
const STRONG_LTR = /[A-Za-zÀ-ɏͰ-ϿЀ-ӿ]/;

export function textDirection(value: string | null | undefined): TextDirection {
  if (!value) return "ltr";
  for (const character of value) {
    if (STRONG_RTL.test(character)) return "rtl";
    if (STRONG_LTR.test(character)) return "ltr";
  }
  return "ltr";
}

export function isRtlText(value: string | null | undefined): boolean {
  return textDirection(value) === "rtl";
}

/**
 * The `dir` and `lang` a value should carry.
 *
 * `lang="ur"` is what picks up the Nastaliq face in `globals.css`; it is only set
 * for right-to-left values, because tagging an English value as Urdu would send a
 * screen reader into the wrong voice.
 */
export function valueTextAttributes(value: string | null | undefined): {
  dir: TextDirection;
  lang?: "ur";
} {
  const dir = textDirection(value);
  return dir === "rtl" ? { dir, lang: "ur" } : { dir };
}
