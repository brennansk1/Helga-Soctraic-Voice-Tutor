# Helga Privacy Policy — v1 (DRAFT, pending counsel review)

_Last updated: 2026-07-01. Policy version: `v1` (referenced by `consent_records.policy_version`)._

## The short version
- **Your child's tutoring never leaves our servers.** All AI runs on hardware we
  operate (self-hosted model inference). No学习 conversation, answer, or progress
  record is sent to any third-party AI provider (B21.3).
- **We collect the minimum needed to teach**: a parent email + password hash,
  each learner's first name/nickname, grade band, optional interests, and their
  learning records (progress, activity, flashcards, exam attempts).
- **Parents are in control**: you can view, export (JSON), and permanently
  delete any learner's records from the parent dashboard at any time
  (FERPA / Utah Student Data Protection Act rights).
- **COPPA**: children under 13 use Helga only under a parent account, after the
  parent grants verifiable consent at signup and at each learner's creation.
  Consent records are versioned and auditable.
- **Health Strand 6 (Human Development)**: this Utah curriculum content is
  blocked for a learner until the parent is notified and grants specific
  consent; withdrawing consent re-blocks it immediately.
- **Safety**: learner messages pass automated safety checks. On a possible
  self-harm or abuse signal we surface crisis resources to the learner and
  alert the parent; we do not store or transmit the learner's words in the alert.
- **Billing** is processed by Stripe; we store only the subscription mirror
  (plan, seats, status) — never card numbers.
- **No advertising, no sale of data, no cross-family visibility.**

## Data we hold, per learner
progress per concept, activity log, flashcards, exam attempts, session state,
accommodations flags, notifications. All rows are keyed to the learner and are
included in the export and purged by deletion.

## Retention
Deleted learners' records are purged immediately from live tables. Encrypted
backups age out on the backup rotation schedule (see ops runbook).

## Contact / Data Manager
The account owner listed in `docs/legal/CONTACTS.md` (to be completed before launch).
