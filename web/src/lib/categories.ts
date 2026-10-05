import { Baby, Files, Flower2, HeartHandshake, ScrollText, type LucideIcon } from "lucide-react";

/**
 * How each kind of certificate looks, everywhere it appears.
 *
 * One place, because the navigation, the home page and the category screens all show
 * the same four things and a clerk should not have to re-learn them between screens.
 *
 * Keyed on the classifier key rather than the name, since the name is editable and an
 * office may rename "Birth" to something in Urdu without meaning to change the icon.
 * A type an administrator invents has no key this knows, and falls back to a neutral
 * scroll - correct rather than guessed at.
 *
 * The death icon is deliberately a flower rather than the skull or headstone an icon
 * set will offer. A clerk opens this screen to do their job beside the family of the
 * person it concerns.
 */
export interface CategoryLook {
  Icon: LucideIcon;
  /** Solid fill, for the selected state. */
  fill: string;
  /** Tint, for the resting card. */
  surface: string;
  /**
   * The same tint as a hover state, written out in full.
   *
   * Not composed from `surface`, because Tailwind reads class names from the source at
   * build time; `hover:${surface}` is assembled at runtime and produces no CSS, so the
   * hover state silently does nothing.
   */
  hover: string;
  /** The hue as ink, for icons and headings at rest. */
  ink: string;
  /** Border in the same hue. */
  border: string;
}

const NEUTRAL: CategoryLook = {
  Icon: ScrollText,
  fill: "bg-primary",
  surface: "bg-primary-surface",
  hover: "hover:bg-primary-surface",
  ink: "text-primary",
  border: "border-primary",
};

/*
 * Written out rather than composed from the key, because Tailwind reads these class
 * names at build time: a string built at runtime produces no CSS at all.
 */
const LOOKS: Record<string, CategoryLook> = {
  BIRTH: {
    Icon: Baby,
    fill: "bg-category-birth",
    surface: "bg-category-birth-surface",
    hover: "hover:bg-category-birth-surface",
    ink: "text-category-birth",
    border: "border-category-birth",
  },
  MARRIAGE: {
    Icon: HeartHandshake,
    fill: "bg-category-marriage",
    surface: "bg-category-marriage-surface",
    hover: "hover:bg-category-marriage-surface",
    ink: "text-category-marriage",
    border: "border-category-marriage",
  },
  DEATH: {
    Icon: Flower2,
    fill: "bg-category-death",
    surface: "bg-category-death-surface",
    hover: "hover:bg-category-death-surface",
    ink: "text-category-death",
    border: "border-category-death",
  },
  OTHER: {
    Icon: Files,
    fill: "bg-category-other",
    surface: "bg-category-other-surface",
    hover: "hover:bg-category-other-surface",
    ink: "text-category-other",
    border: "border-category-other",
  },
};

export function categoryLook(classifierKey: string | null | undefined): CategoryLook {
  return (classifierKey && LOOKS[classifierKey]) || NEUTRAL;
}
