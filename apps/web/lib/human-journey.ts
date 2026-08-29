export const HUMAN_JOURNEY = [
  { id: "foundation", number: "01", label: "Foundation", detail: "Choose the document" },
  { id: "shape", number: "02", label: "Shape", detail: "Write the essential signals" },
  { id: "review", number: "03", label: "Review", detail: "Read the rendered document" },
  { id: "release", number: "04", label: "Download", detail: "Validate and keep the file" }
] as const;

export type HumanJourneyStage = (typeof HUMAN_JOURNEY)[number]["id"];

export function humanJourneyPosition(stage: HumanJourneyStage) {
  const index = HUMAN_JOURNEY.findIndex((step) => step.id === stage);
  const current = index === -1 ? 1 : index + 1;
  return { current, total: HUMAN_JOURNEY.length, percent: Math.round((current / HUMAN_JOURNEY.length) * 100) };
}
