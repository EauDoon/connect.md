import type { ApplicationDocument } from "@/lib/recruitment-api";

export type ApplicationDocumentInventoryResult =
  | {
      status: "ready";
      documents: ApplicationDocument[];
      selected: string;
    }
  | {
      status: "error";
      documents: [];
      selected: "";
      error: string;
    };

export async function loadApplicationDocumentInventory(
  load: () => Promise<ApplicationDocument[]>,
  presentError: (error: unknown) => string,
): Promise<ApplicationDocumentInventoryResult> {
  try {
    const documents = (await load()).filter(
      (document) => document.visibility === "public",
    );
    return {
      status: "ready",
      documents,
      selected: documents[0]
        ? `${documents[0].kind}:${documents[0].identifier}`
        : "",
    };
  } catch (error) {
    return {
      status: "error",
      documents: [],
      selected: "",
      error: presentError(error),
    };
  }
}
