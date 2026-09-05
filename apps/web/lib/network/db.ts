/**
 * Postgres client and migration runner for the network MVP.
 *
 * The connection string comes from CONNECTMD_NETWORK_DATABASE_URL. When it
 * is absent, `networkDatabaseConfigured()` is false and every network route
 * answers 503 with an explicit configuration contract instead of crashing —
 * guest-only deploys stay green without a database.
 *
 * Connection URLs may carry gringotts:// references; resolution happens at
 * deploy time (gringotts run/export), never inside request handling.
 */

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import postgres from "postgres";

const MIGRATIONS_DIRECTORY = join(dirname(fileURLToPath(import.meta.url)), "migrations");

export const DATABASE_URL_ENV = "CONNECTMD_NETWORK_DATABASE_URL";

let client: postgres.Sql | null = null;
let clientKey: string | null = null;

export function networkDatabaseUrl(): string | null {
  const raw = process.env[DATABASE_URL_ENV];
  if (typeof raw !== "string" || raw.trim() === "" || raw.startsWith("gringotts://")) {
    // A gringotts:// reference in runtime config means deployment-time
    // resolution failed; treat it exactly like a missing configuration.
    return null;
  }
  return raw;
}

export function networkDatabaseConfigured(): boolean {
  return networkDatabaseUrl() !== null;
}

export function database(): postgres.Sql {
  const url = networkDatabaseUrl();
  if (url === null) {
    throw new NetworkUnavailableError();
  }
  const key = url;
  if (client === null || clientKey !== key) {
    client?.end({ timeout: 1 });
    client = postgres(url, {
      max: 5,
      idle_timeout: 20,
      connect_timeout: 5,
      prepare: false,
      onnotice: () => undefined,
    });
    clientKey = key;
  }
  return client;
}

export class NetworkUnavailableError extends Error {
  constructor() {
    super("network database is not configured");
    this.name = "NetworkUnavailableError";
  }
}

/** Apply any not-yet-applied migration files, in name order, in one transaction each. */
export async function migrate(): Promise<readonly string[]> {
  const sql = database();
  await sql`CREATE TABLE IF NOT EXISTS network_schema_migrations (
    name TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
  )`;
  const applied = new Set(
    (await sql`SELECT name FROM network_schema_migrations`).map((row) => row.name as string),
  );
  const files = readdirSync(MIGRATIONS_DIRECTORY).filter((name) => name.endsWith(".sql")).sort();
  const ran: string[] = [];
  for (const file of files) {
    if (applied.has(file)) continue;
    const statements = readFileSync(join(MIGRATIONS_DIRECTORY, file), "utf8");
    await sql.begin(async (tx) => {
      await tx.unsafe(statements);
      // Files include their own migration-bookkeeping INSERT; nothing else to do.
    });
    ran.push(file);
  }
  return ran;
}

export async function closeDatabase(): Promise<void> {
  if (client !== null) {
    await client.end({ timeout: 1 });
    client = null;
    clientKey = null;
  }
}
