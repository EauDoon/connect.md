import { PublicRouteLoading } from "@/components/public-route-loading";

export default function LoadingJobs() {
  return <PublicRouteLoading className="mx-auto max-w-7xl px-5 py-14 lg:px-8" label="Loading service-gated jobs."><div className="h-4 w-32 rounded bg-white/10" /><div className="mt-5 h-20 max-w-3xl rounded-2xl bg-white/10" /><div className="mt-10 h-28 rounded-2xl bg-white/10" /><div className="mt-5 h-36 rounded-2xl bg-white/10" /></PublicRouteLoading>;
}
