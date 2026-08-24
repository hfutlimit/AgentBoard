# Auth feature

Owns HTTP authentication adapters: registration, login, current-user/profile,
password changes, and API-key routes.  The route layer delegates identity
operations to `features.identity` services and keeps HTTP request schemas in
this package.

