# App Store launch notes

## Tommy's steps (only he can do these)
1. Accept Apple's new license agreement at developer.apple.com (builds are blocked until then).
2. Run `supabase/migrations/20261220_app_store_ready.sql` in the Supabase SQL editor (delete account, report answer, screen time).
3. Repo Settings > Pages > Source: **GitHub Actions** (the privacy and support pages publish from `site/`).
4. Pick the support email to publish on the support and privacy pages (`SUPPORT_EMAIL` placeholder in `site/`).
5. App Store Connect > App Privacy: enter the answers below (Apple keeps this web-only).
6. Run **App Store** workflow (screenshots on, submit off) to upload the listing, then check it and run again with submit on.

## App Privacy answers
Tracking: **No** (no data is used to track people across other companies' apps or websites).

| Data type | Collected for | Linked to the user | Used for tracking |
|---|---|---|---|
| Contact Info > Email Address | App Functionality | Yes | No |
| Identifiers > User ID | App Functionality | Yes | No |
| User Content > Other User Content (GM questions, reports, team picture) | App Functionality | Yes | No |
| Usage Data > Product Interaction (screen time) | Analytics, App Functionality | Yes | No |
| Diagnostics > Crash Data | App Functionality | Yes | No |

Not collected: location, contacts, health, financial info, purchases, browsing history, search history, advertising data.
Age rating answers are in `mobile/store/age_rating.json` (all "None"; 4+).

## Free-tier risks
- **Supabase (free):** pauses a project after a week with no activity. The 15-minute Sleeper sync and daily jobs keep it awake; if those stop in the offseason, it can pause. 500 MB database cap: screen time adds roughly 1 MB a month for a 12-team league.
- **Cloud Run (free):** 2 million requests and 180,000 vCPU-seconds a month. Live pages poll every 10 s only while open, which fits for one league. Max 3 copies, no always-on instance, $1 budget alert.
- **Expo (free):** EAS Update is free up to 1,000 monthly users. iOS builds go through GitHub Macs (free for public repos), so Expo's 15 free builds a month don't limit us.
- **GitHub Actions:** free and unlimited for a public repo. Logs are public, so jobs print numbers only.
- **Anthropic:** not free. Each owner is capped at 30 GM answers a day (now counted from the usage log, so it survives server restarts). Worst case is about $3 per owner per day; turn on auto-reload with a monthly limit.

## Sleeper's terms
- API docs: "free to use for non-commercial purposes"; commercial use needs a license. Stay under 1,000 calls a minute.
- Terms of Use (Oct 6, 2026): no automated access or third-party apps that pull Sleeper data "for that third-party's commercial or business purposes" without written consent.
- Our app is free, has no ads and serves one private league, so it fits "non-commercial". A public App Store listing is still more exposure than a private tool, so an **unlisted** App Store link or public TestFlight link is lower-risk.
- Remaining Sleeper ties: league data sync, projections, owner avatars from sleepercdn.com (seeded into our own profiles already), and "Open in Sleeper" trade buttons. All go away when the league leaves Sleeper after the 2026 season.

## How updates ship after launch
- Server and data changes: Ship app workflow (server). No review, nobody updates.
- Screen and design changes: **App update (no review)** workflow (EAS Update). Phones get it on the next launch. Only works for builds 86 and later.
- Native changes (new native packages, icon, widgets, permissions): a new build through Ship app, then Apple review.
