# ADR-0002 · Redaction and retention

- Status: Accepted (A0)
- Date: 2026-09-04
- Applies to: Node local store, command/result/event payloads, artifact
  references, logs, traces and the Local Portal

## Context

doc 150 PR-015 and NFR-008, and doc 151 §10, fix the minimum exposure rules:

- secrets, tokens, prompts, stdout, tool input/output and file contents are
  classified and redacted;
- secret access is confined to the Node provider adapter;
- local detail, Server summary and artifact references have different access
  policies;
- audit records contain no secret;
- error messages, traces, MQ headers, exception text and Portal URLs must not
  become a leak channel.

doc 151 §9.2 also puts credential lookup, local redaction and retention
settings on the provider adapter rather than on a shared layer, precisely so
that no generic component ever holds a secret.

## Decision

**The boundary is structural before it is procedural.** `ResultEnvelope` has no
field capable of carrying a prompt, a credential, full stdout, tool payloads or
file contents. That absence is enforced by a contract test, not by a code
review convention. Large evidence travels as `ArtifactReference` — uri,
sha256, size, optional expiry — never inline.

**Inline payloads are bounded.** `PayloadLimits` caps the inline command
payload at 64 KiB and the persisted outcome summary at 8 KiB. Exceeding either
is a validation error directing the caller to a reference, so the limit cannot
be exceeded by accident.

**Redaction happens before every egress, not at the source.** The same
redaction is applied to logs, trace attributes, MQ headers, exception messages
and the Local Portal render. Redacting only where data is produced leaves the
error path — the most likely place for a raw token to appear — uncovered.

**Retention is explicit per artifact.** `ArtifactReference.ExpiresAt` is
nullable, and null means "retained until policy deletes it", which is
deliberately not the same as "available forever". `IsExpired(now)` is the only
evaluation of that window.

**The Local Portal is the only surface that shows detail, and it still
redacts.** doc 151 §8.2 permits the Local Portal to show stdout, tool and file
metadata because the data never leaves the machine, but §8.2 also requires
secret detection, redaction, access control and retention there. Raw detail is
not a special case that skips redaction.

## Consequences

- The Server cannot receive Node detail even if a future change wants to send
  it; there is nowhere to put it in the contract.
- Provider-specific redaction and retention rules live in
  `ProviderLocalSettings` on the Node, because only the provider adapter knows
  what its output contains (doc 151 §9.2).
- An artifact without a well-formed sha256 is rejected: a handoff cannot
  distinguish a corrupted artifact from a changed one without a checksum, and
  without an expiry the store grows without bound.
- Debugging gets harder in one specific way: an operator chasing a provider
  failure on the Server side will see a summary and a category, and must open
  the Local Portal for detail. That is the intended cost of the boundary, not a
  gap to be optimised away.
