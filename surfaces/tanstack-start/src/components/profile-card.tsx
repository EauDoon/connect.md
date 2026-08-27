import { Link } from '@tanstack/react-router'
export function ProfileCard({ handle, display_name, headline, location, kind = 'person' }: {
  handle: string; display_name: string; headline?: string | null; location?: string | null; kind?: 'person' | 'agent' | 'org'
}) {
  const to = kind === 'agent' ? `/agents/${handle}` : kind === 'org' ? `/orgs/${handle}` : `/p/${handle}`
  return (
    <Link to={to} className="card block no-underline hover:border-ink/25 min-h-[8.5rem] h-full">
      <div className="text-[11px] uppercase tracking-wider text-ink-muted mb-2">{kind}</div>
      <div className="font-serif text-xl font-semibold text-ink">{display_name}</div>
      {headline ? <p className="text-sm text-ink mt-1">{headline}</p> : null}
      <p className="text-sm text-ink-muted mt-2">{location ? `${location} · ` : ''}@{handle}</p>
    </Link>
  )
}
