# Feature — Authentication

## 1. Requirement

Secure administrator sign-in (§6.1, §40). One role
([ADR-001](../architecture/decisions/ADR-001-single-admin-role.md)).

## 2. Business rules

- Only an authenticated admin may reach any endpoint other than login and health.
- **There is no registration endpoint.** The first admin is created by `scripts/create_admin.py`.
- `is_active = false` blocks login without deleting the account.
- Wrong email and wrong password return the **same** code and message, so the response cannot be
  used to enumerate accounts.
- Every login attempt, successful or not, is audited.

## 3. User flow

```
/login → email + password → POST /auth/login
   ├─ success → tokens issued → redirect /dashboard
   └─ failure → inline error, field values preserved, password cleared
```

Session expiry mid-use: the BFF silently refreshes; if the refresh is dead, the user lands back
on `/login` with a "session expired" notice and their intended path preserved for return.

## 4. Backend flow

```
POST /auth/login
  ├─ rate limit  5/min/IP
  ├─ normalise email to lowercase
  ├─ load user  → not found: hash a dummy password anyway, then fail
  ├─ verify hash (bcrypt)
  ├─ check is_active
  ├─ issue access (30 min) + refresh (7 days)
  ├─ update last_login_at
  ├─ audit  auth.login  or  auth.login_failed
  └─ 200 + tokens
```

The dummy-hash step on a missing user is deliberate: without it, a missing account returns
measurably faster than a wrong password, and the timing difference leaks which emails exist.

## 5. Frontend flow

Login is a Next.js **server action / route handler**. The browser posts to Next.js, Next.js calls
FastAPI, stores the refresh token in an `httpOnly` cookie, and keeps the access token
server-side. **No token ever reaches client JavaScript** (§40, §42).

## 6. Database impact

`users` (read, `last_login_at` write) and `audit_logs` (insert). No migration beyond initial
creation.

## 7. API contract

`POST /auth/login`

```json
{ "email": "admin@example.com", "password": "…" }
```

```json
{ "success": true,
  "data": { "access_token": "…", "token_type": "bearer", "expires_in": 1800,
            "user": { "id": "…", "email": "…", "full_name": "…" } } }
```

Refresh token is set as a cookie, not returned in the body.

Also: `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me`.

## 8. Celery impact

None.

## 9. Qdrant impact

None.

## 10. Redis impact

Revoked refresh tokens are held in a Redis denylist until natural expiry, so logout takes effect
immediately rather than at token expiry.

## 11. Security considerations

- Passwords hashed with bcrypt (cost ≥ 12). The plaintext is never logged, never stored, never
  echoed.
- Refresh cookie: `httpOnly`, `Secure`, `SameSite=Strict`.
- Login rate-limited per IP.
- Uniform failure response (see business rules).
- Constant-time comparison for password verification.
- JWT signed with `HS256` and a secret from `.env` — never committed, never client-side.
- **The auth middleware's public-path set is exact-match, never a prefix test.** A
  `startswith()` exemption would silently exempt any router sharing the prefix. See
  [security/security-model.md](../security/security-model.md).

## 12. Error handling

`INVALID_CREDENTIALS` 401 · `ACCOUNT_INACTIVE` 403 · `TOKEN_EXPIRED` 401 ·
`REFRESH_TOKEN_INVALID` 401 · `RATE_LIMIT_EXCEEDED` 429 · `UNAUTHORIZED` 401.

## 13. Edge cases

| Case | Behaviour |
| ---- | --------- |
| Email differing only in case | Matches — normalised on write and read |
| Deactivated mid-session | Access token stays valid until expiry (≤30 min); refresh fails |
| Two tabs, one logs out | The other fails on next refresh and returns to login |
| Clock skew | 30 s leeway on `exp` validation |
| Refresh replay after logout | Rejected via the Redis denylist |
| Password with leading/trailing spaces | Preserved — never trimmed |

## 14. Testing requirements

Valid login · wrong password · unknown email (same response and comparable timing) · inactive
account · expired access token · refresh rotation · logout then refresh (must fail) · rate limit
trips at the 6th attempt · no token in any client-visible payload · audit row written for both
outcomes.

## 15. Acceptance criteria

- [ ] Admin signs in and reaches the dashboard
- [ ] No endpoint other than login/health is reachable without a token
- [ ] Wrong email and wrong password are indistinguishable in body, status and timing
- [ ] No access or refresh token appears in browser-accessible storage or JS
- [ ] Logout immediately invalidates the refresh token
- [ ] Login attempts appear in `audit_logs`
- [ ] Rate limit returns 429 with `Retry-After`
