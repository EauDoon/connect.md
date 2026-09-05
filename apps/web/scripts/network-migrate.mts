// Applies pending network MVP migrations to CONNECTMD_NETWORK_DATABASE_URL.
// Refuses to run without an explicit database URL; gringotts:// references
// fail closed (resolve them at deploy time with gringotts run/export).
import { migrate, networkDatabaseConfigured, DATABASE_URL_ENV } from "../lib/network/db.ts";

if (!networkDatabaseConfigured()) {
  console.error(`network migrate: ${DATABASE_URL_ENV} is not set (or is an unresolved gringotts:// reference)`);
  process.exit(1);
}
const applied = await migrate();
console.log(applied.length === 0 ? "network migrate: already up to date" : `network migrate: applied ${applied.join(", ")}`);
process.exit(0);
