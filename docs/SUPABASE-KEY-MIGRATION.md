# Supabase API key and signing-key migration

This is a **deployment runbook**, not a record that the migration occurred.
NEXUS supports Supabase's opaque publishable/secret API keys and ES256 access
tokens after the code in this branch is deployed. The existing HS256 mode is
still the default for local development and existing deployments. Do not
switch production signing keys until the ES256-capable backend is ready.

A leaked legacy `service_role` key bypasses RLS. Do not share it, use it for
new deployments, or assume disabling the legacy API keys alone completely
retires it: Supabase states that the legacy signing secret must also be revoked.
If active misuse is suspected, deactivate the legacy keys immediately and
accept downtime. Follow Supabase's [incident guide](https://supabase.com/docs/guides/security/rotate-leaked-api-keys).

## How the new configuration works

* `SUPABASE_JWT_ALGORITHM=ES256` pins the algorithm and verifies access tokens
  with the configured project's `/auth/v1/.well-known/jwks.json`, not a URL from
  a token. It checks issuer, audience, expiry, subject and authenticated role.
  JWKS is cached up to 60 seconds per API process; an unavailable key endpoint
  fails closed. Supabase's own JWKS caches can also delay propagation.
* `SUPABASE_PUBLISHABLE_KEY` and `SUPABASE_SECRET_KEY` take precedence over
  `SUPABASE_ANON_KEY` and `SUPABASE_SERVICE_ROLE_KEY`, respectively. The
  secret key remains server-only. Storage sends an opaque key in `apikey`, not
  `Authorization: Bearer`.
* The frontend already accepts the publishable key as the value of its existing
  `PUBLIC_SUPABASE_ANON_KEY` variable. It is public by design. **Never** put
  `SUPABASE_SECRET_KEY`, the database URL, or the AI key in a `PUBLIC_` variable
  or a browser environment.

## Cutover overview

This requires coordination: switching the signing key while the old backend is
still running will break API authentication. After a leaked legacy key, there
is a security/availability tradeoff; don't prolong the transition needlessly.

1. Prepare, review and deploy this code; retain an emergency rollback plan. If
   legacy API keys were already disabled, frontend login will be unavailable
   until the replacement publishable key is deployed.
2. In Supabase Settings → API Keys, use a publishable key and a secret key.
   Create a dedicated secret key if appropriate. Do not paste secret values in
   chat, screenshots, Git, or command history. The legacy `service_role` key
   must not be reused. Configure the Fly app with `SUPABASE_URL`,
   `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`, `DATABASE_URL`,
   `OPENAI_API_KEY`, `CORS_ALLOWED_ORIGINS`, and
   `SUPABASE_JWT_ALGORITHM=ES256`. No `SUPABASE_JWT_SECRET` is needed in ES256
   mode. `fly secrets list` shows names/digests, not values.
3. In Supabase Settings → JWT Keys, create an **ES256** standby signing key
   and rotate it to current. Follow [Supabase's signing-key guide](https://supabase.com/docs/guides/auth/signing-keys).
   Old HS256 tokens will not work against the new ES256-only backend; users
   should sign in again. The previous secret remains trusted by Supabase until
   it is explicitly revoked. Do not assume rotating alone revokes it.
4. Deploy the backend from the updated repository, explicitly targeting the
   intended Fly app (for example `fly deploy --app earnest-surf-616`). Verify
   both web and worker machines, `/health` and `/ready`, and worker logs.
5. On Vercel set `PUBLIC_SUPABASE_ANON_KEY` to the **publishable** key and
   `API_BASE_URL` to the actual Fly HTTPS origin, then redeploy if needed.
   Test fresh sign-in, workspace access, upload and processing end to end.
6. Disable legacy API keys if not already done and revoke the previous legacy
   JWT secret in Supabase. Supabase requires legacy keys to be disabled first.
   Check the dashboard state, then retest and monitor errors. Retiring legacy
   credentials invalidates clients still using them; don't re-enable a leaked
   key as a routine rollback. Check other integrations before revoking.

This repository has not verified a live rotation, Fly deploy, or new-key
Storage request. Test on a suitable non-production project first where
possible; do not claim the incident resolved until revocation is verified.
