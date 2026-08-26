import { PublicRouteLoading } from "@/components/public-route-loading";

export default function LoadingDiscover() {
  return <PublicRouteLoading className="mx-auto max-w-7xl px-5 py-14 lg:px-8" label="Loading public discovery."><div className="h-4 w-28 rounded bg-white/10" /><div className="mt-5 h-20 max-w-4xl rounded-2xl bg-white/10" /><div className="mt-8 h-14 max-w-2xl rounded-2xl bg-white/10" /><div className="mt-10 grid gap-4 md:grid-cols-2"><div className="h-40 rounded-2xl bg-white/10" /><div className="h-40 rounded-2xl bg-white/10" /></div></PublicRouteLoading>;
}
