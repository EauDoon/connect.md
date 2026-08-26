import { PublicRouteLoading } from "@/components/public-route-loading";

export default function RepresentativesLoading() {
  return <PublicRouteLoading className="mx-auto max-w-7xl px-5 py-12 lg:px-8 lg:py-16" label="Loading public representative declarations."><p className="eyebrow">Public representative discovery</p><div className="mt-5 h-16 max-w-3xl rounded-2xl bg-white/[.08]" /><div className="mt-10 grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]"><div className="space-y-4">{[0, 1, 2].map((item) => <div key={item} className="h-44 rounded-3xl border border-white/10 bg-panel" />)}</div><div className="h-64 rounded-3xl border border-white/10 bg-panel" /></div></PublicRouteLoading>;
}
