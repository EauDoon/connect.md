import { PublicRouteLoading } from "@/components/public-route-loading";

export default function PublicProfileLoading() {
  return (
    <PublicRouteLoading className="mx-auto max-w-4xl px-5 py-16 lg:px-8" label="Loading public profile.">
      <div className="h-4 w-20 rounded bg-white/10" />
      <div className="mt-10 rounded-3xl border border-white/10 bg-panel p-10"><div className="h-4 w-28 rounded bg-acid/15" /><div className="mt-5 h-14 max-w-md rounded bg-white/10" /><div className="mt-5 h-5 max-w-sm rounded bg-white/10" /><div className="mt-12 space-y-3"><div className="h-4 rounded bg-white/10" /><div className="h-4 w-11/12 rounded bg-white/10" /><div className="h-4 w-4/5 rounded bg-white/10" /></div></div>
    </PublicRouteLoading>
  );
}
