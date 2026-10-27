# Legacy Dynasty iPhone app

Expo (React Native) app for the league. It signs in with the same Supabase
accounts as the website and reads data as the signed-in owner, so row-level
security applies the same way. Python-only logic and secret keys live in the
Legacy API server (`../api`), never in this app.

## Run it on your iPhone

1. `cd mobile && npm install`
2. Copy `.env.example` to `.env.local` and fill in the Supabase URL and anon key
   (the same public values the website uses).
3. `npx expo start`, then scan the QR code with the iPhone camera (Expo Go app).

Checks: `npx tsc --noEmit`.
