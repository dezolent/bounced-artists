# Complete database foundation plan

The existing `artists` and `core` apps remain the identity foundation. Add `organizations` ownership, then `catalog`, `rights`, `events`, `provenance`, `publishing`, and `analytics` in that dependency order. Each app gets additive migrations, searchable staff administration, and focused PostgreSQL tests.

An organization owns private records. Credited artists may cross organizations without granting private access. Globally reusable service, genre, society, and platform references carry no artist authorization. Typed foreign keys protect evidence targets. The existing private API remains limited to membership-scoped artist summaries.

Before production migration: confirm a completed backup, inspect current artist state, verify local migrations and constraints on PostgreSQL, then deploy through the existing PR and check migration/readiness logs. No website catalog data is imported in this task.
