import { PublicRouteLoading } from "@/components/public-route-loading";

export default function SearchLoading() {
  return <PublicRouteLoading className="mx-auto max-w-7xl px-5 py-16 lg:px-8" label="Loading directory results."><p className="eyebrow">Directory</p><div className="mt-5 h-16 max-w-3xl rounded-2xl bg-white/[.07]" /><div className="mt-10 grid gap-4 lg:grid-cols-[15rem_1fr]"><div className="h-72 rounded-2xl bg-white/[.04]" /><div className="space-y-4">{[1, 2, 3].map((item) => <div key={item} className="h-44 rounded-3xl bg-white/[.05]" />)}</div></div></PublicRouteLoading>;
}
