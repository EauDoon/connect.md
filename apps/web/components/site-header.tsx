"use client";

import { SignInButton, UserButton } from "@clerk/nextjs";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { LayoutGrid, Menu, Settings, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { useConnectmdAuth } from "@/components/auth-provider";
import { accountLifecycleFeatureEnabled } from "@/lib/account-lifecycle-api";
import { PRIMARY_NAVIGATION, PUBLIC_PRIMARY_NAVIGATION, PUBLIC_UTILITY_NAVIGATION } from "@/lib/navigation";
import { bindEscapeToCloseMobileNavigation, closeMobileNavigationAndRestoreFocus } from "@/lib/mobile-navigation";
import { cn } from "@/lib/utils";

function NavigationLink({ href, label, active, onNavigate, mobile = false }: { href: string; label: string; active: boolean; onNavigate?: () => void; mobile?: boolean }) {
  return <Link
    href={href}
    aria-current={active ? "page" : undefined}
    onClick={onNavigate}
    className={cn(
      "rounded-xl font-medium transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid",
      mobile ? "flex min-h-12 items-center justify-between border border-white/10 px-4 text-sm" : "inline-flex min-h-11 items-center rounded-full px-3 text-sm",
      active ? "bg-white/10 text-white" : "text-mist hover:bg-white/[.06] hover:text-white"
    )}
  >
    {label}
    {mobile && <span aria-hidden className="text-acid">↗</span>}
  </Link>;
}

export function SiteHeader({ privateWorkspacesEnabled = false }: { privateWorkspacesEnabled?: boolean }) {
  const pathname = usePathname();
  const { configured, isLoaded, isSignedIn } = useConnectmdAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  const mobileToggleRef = useRef<HTMLButtonElement>(null);
  const reducedMotion = useReducedMotion();
  const lifecycleEnabled = accountLifecycleFeatureEnabled();
  const privateNavigationEnabled = privateWorkspacesEnabled && configured;
  const primaryNavigation = privateNavigationEnabled ? PRIMARY_NAVIGATION : PUBLIC_PRIMARY_NAVIGATION;
  const closeMobileNavigation = useCallback(() => setMobileOpen(false), []);
  const isNavigationLinkActive = useCallback((href: string) => href === "/human" ? pathname === "/human" || pathname === "/md" : pathname === href, [pathname]);

  useEffect(() => {
    if (!mobileOpen) return;
    return bindEscapeToCloseMobileNavigation(window, () => closeMobileNavigationAndRestoreFocus(() => closeMobileNavigation(), mobileToggleRef.current));
  }, [closeMobileNavigation, mobileOpen]);

  return (
    <header className="sticky top-0 z-30 border-b border-white/[.07] bg-ink/80 backdrop-blur-xl">
      <nav aria-label="Primary navigation" className="mx-auto flex h-16 max-w-7xl items-center justify-between px-0 min-[300px]:px-3 sm:px-5 lg:px-8">
        <Link href="/" onClick={() => closeMobileNavigation()} className="group inline-flex min-h-11 min-w-11 items-center justify-center gap-2 rounded-lg text-base font-semibold tracking-tight text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-acid min-[240px]:justify-start">
          <span aria-hidden className="grid size-6 place-items-center rounded-md bg-acid text-xs font-black text-ink transition group-hover:rotate-[-8deg]">C</span>
          <span className="max-[239px]:sr-only">connect.md</span>
        </Link>
        <div className="flex items-center gap-1">
          <div className="hidden items-center gap-0.5 lg:flex" aria-label="Product navigation">
            {primaryNavigation.map((link) => <NavigationLink key={link.href} {...link} active={isNavigationLinkActive(link.href)} />)}
          </div>
          <div className="hidden items-center border-l border-white/10 pl-1 lg:flex" role="group" aria-label="Trust and data navigation">
            {PUBLIC_UTILITY_NAVIGATION.map((link) => <NavigationLink key={link.href} {...link} active={isNavigationLinkActive(link.href)} />)}
          </div>
          {privateNavigationEnabled && <div className="ml-1 border-l border-white/10 pl-2">
            {!isLoaded ? <span className="block size-11 animate-pulse rounded-full bg-white/10" role="status" aria-label="Loading account" /> : isSignedIn ? <div className="flex items-center gap-1"><Link href="/workspace" aria-label="Your workspace" aria-current={pathname === "/workspace" ? "page" : undefined} className="hidden min-h-11 items-center gap-2 rounded-full border border-white/15 px-3 text-sm font-semibold text-white transition hover:bg-white/[.08] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid sm:inline-flex"><LayoutGrid className="size-4" aria-hidden /><span className="sr-only xl:not-sr-only">Workspace</span></Link><UserButton appearance={{ elements: { avatarBox: "size-11" } }} />{lifecycleEnabled && <Link href="/account" aria-label="Account privacy" aria-current={pathname === "/account" ? "page" : undefined} className="hidden min-h-11 items-center gap-2 rounded-full border border-white/15 px-3 text-sm font-semibold text-white transition hover:bg-white/[.08] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid sm:inline-flex"><Settings className="size-4" aria-hidden /><span className="sr-only xl:not-sr-only">Account</span></Link>}</div> : <SignInButton mode="modal"><button type="button" className="min-h-11 rounded-full border border-white/15 px-3 text-sm font-semibold text-white transition hover:bg-white/[.08] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid">Sign in</button></SignInButton>}
          </div>}
          <button ref={mobileToggleRef} type="button" aria-label={mobileOpen ? "Close navigation" : "Open navigation"} aria-controls="mobile-primary-navigation" aria-expanded={mobileOpen} onClick={() => setMobileOpen((open) => !open)} className="inline-flex size-11 items-center justify-center rounded-full border border-white/15 text-white transition hover:bg-white/[.08] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-acid lg:hidden">
            {mobileOpen ? <X className="size-5" aria-hidden /> : <Menu className="size-5" aria-hidden />}
          </button>
        </div>
      </nav>
      <AnimatePresence initial={false}>
        {mobileOpen && <motion.nav
          id="mobile-primary-navigation"
          aria-label="Mobile primary navigation"
          initial={reducedMotion ? false : { opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: "auto" }}
          exit={reducedMotion ? undefined : { opacity: 0, height: 0 }}
          transition={{ duration: reducedMotion ? 0 : 0.2, ease: "easeOut" }}
          className="overflow-hidden border-t border-white/[.07] lg:hidden"
        >
          <div className="mx-auto grid max-w-7xl gap-2 px-5 py-4">
            {primaryNavigation.map((link) => <NavigationLink key={link.href} {...link} mobile active={isNavigationLinkActive(link.href)} onNavigate={() => closeMobileNavigation()} />)}
            <div className="mt-1 border-t border-white/10 pt-3" role="group" aria-label="Trust and data navigation">
              {PUBLIC_UTILITY_NAVIGATION.map((link) => <NavigationLink key={link.href} {...link} mobile active={isNavigationLinkActive(link.href)} onNavigate={() => closeMobileNavigation()} />)}
            </div>
            {privateNavigationEnabled && isLoaded && isSignedIn && <NavigationLink href="/workspace" label="Workspace" mobile active={isNavigationLinkActive("/workspace")} onNavigate={() => closeMobileNavigation()} />}
            {privateNavigationEnabled && isLoaded && isSignedIn && lifecycleEnabled && <NavigationLink href="/account" label="Account privacy" mobile active={isNavigationLinkActive("/account")} onNavigate={() => closeMobileNavigation()} />}
          </div>
        </motion.nav>}
      </AnimatePresence>
    </header>
  );
}
