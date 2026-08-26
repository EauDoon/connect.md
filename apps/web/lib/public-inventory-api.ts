import { apiRequest } from "@/lib/api";
import { PRODUCT_ENDPOINTS } from "@/lib/product-endpoints";
import type { PublicDocumentInventoryItem } from "@/lib/product-types";

export async function listPublicDocumentInventory(cursor?: string | null): Promise<{ items: PublicDocumentInventoryItem[]; nextCursor: string | null }> {
  const params = new URLSearchParams({ limit: "200" });
  if (cursor) params.set("cursor", cursor);
  const raw = asRecord(await apiRequest<unknown>(`${PRODUCT_ENDPOINTS.publicDocuments}?${params.toString()}`, { server: true }));
  if (!Array.isArray(raw.items)) throw new Error("The public-document inventory returned an invalid items list.");
  const nextCursor = raw.next_cursor;
  if (nextCursor !== null && (typeof nextCursor !== "string" || !nextCursor)) {
    throw new Error("The public-document inventory returned an invalid cursor.");
  }
  return {
    items: raw.items.map((item): PublicDocumentInventoryItem => {
      const record = asRecord(item);
      if ((record.kind !== "profile" && record.kind !== "resume") || typeof record.slug !== "string" || !record.slug || typeof record.updated_at !== "string" || Number.isNaN(new Date(record.updated_at).valueOf())) {
        throw new Error("The public-document inventory returned an invalid item.");
      }
      return { kind: record.kind, slug: record.slug, updatedAt: record.updated_at };
    }),
    nextCursor
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
