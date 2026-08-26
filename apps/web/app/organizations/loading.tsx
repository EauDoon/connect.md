import { PublicRouteLoading } from "@/components/public-route-loading";

export default function LoadingOrganizations() {
  return <PublicRouteLoading className="mx-auto max-w-7xl px-5 py-14 lg:px-8" label="Loading service-gated organizations."><div className="h-4 w-40 rounded bg-white/10" /><div className="mt-5 h-20 max-w-3xl rounded-2xl bg-white/10" /><div className="mt-12 grid gap-4 sm:grid-cols-2"><div className="h-44 rounded-2xl bg-white/10" /><div className="h-44 rounded-2xl bg-white/10" /></div></PublicRouteLoading>;
}
