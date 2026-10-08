import { loadPublishedProfile } from "./published-profile";

/**
 * The lookup runs here, not only in the page, because loading.tsx wraps this
 * segment's page in a Suspense boundary: a notFound() raised inside it is
 * caught after the 200 shell has streamed. A layout renders outside its own
 * loading boundary, so a not-found raised here answers HTTP 404 with noindex
 * to every client, crawlers included.
 */
export default async function PublicProfileLayout({ children, params }: { children: React.ReactNode; params: Promise<{ handle: string }> }) {
  const { handle } = await params;
  await loadPublishedProfile(handle);
  return children;
}
