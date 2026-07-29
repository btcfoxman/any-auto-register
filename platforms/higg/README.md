# Higgsfield

The `higg` platform implements the observed Clerk email registration flow:

1. Solve the invisible Clerk Turnstile challenge.
2. Create a Clerk sign-up.
3. Request and verify the six-digit email code.
4. Select the created Clerk session and persist its short-lived JWT and cookies.
5. Query the Higgsfield wallet and Seedance free-generation state when the
   current DataDome context permits it.
6. Sync the account to a configured Higg2API service.

## Configuration

- `higg2api_url`
- `higg2api_api_key`
- `higg2api_max_concurrency`
- `higg2api_enable_auto_maintenance`

Registration requires a configured Turnstile provider. Clerk registration may
succeed while the FNF generation API is still blocked by DataDome or
Cloudflare. In that case the account remains valid but
`generation_ready=false`; import fresh browser cookies, DataDome client ID, and
matching browser fingerprint fields before using it for generation.

The Clerk JWT expires quickly. Higg2API refreshes it from the stored Clerk
session before account checks and generation requests.
