# Testing Policy — Analecta

> Living document. Defines what a test must demonstrate before a change to
> Analecta's Python code is considered done (scope at the end). Complements
> `docs/quality-gate.md` (the mechanical gate — ruff, basedpyright, pytest, branch
> coverage) and `docs/dependency-verification.md` §5 (smoke-testing a bumped
> package the gate never exercises).

---

## The rule in one line

A passing test proves less than a failing one that was understood. Before a change
under `backend/src/analecta/**` is done, its tests must have demonstrated the
failure they exist to catch — not merely gone green.

## Why not test-first

Test-first ordering (write the failing test, then the code) is not required here.
Its value is not the ordering itself but the moment in the middle: a failing test,
observed and explained, before the fix exists. When a single pass writes both the
test and the code it checks, the ordering is trivial to satisfy while skipping that
moment entirely — a test written against already-working code can pass on the first
run without ever touching the behaviour it was meant to protect.

So the policy keeps the part that carries information and drops the ceremony: the
test must be shown to fail for the right reason, whenever a state exists in which it
could.

## What "demonstrated" means

The requirement splits by one question: **is there a pre-change state in which this
test is red?**

### Arm A — a meaningful pre-change state exists

This is the common case: a new guard, a new validation, a new error path, or a
change to existing behaviour. There is a concrete "before" — the code without the
change.

At least one new or changed test must be shown red against that "before" state, and
its failure must be on the assertion that targets the specific mechanism the change
introduces — not a neighbouring path that was already safe. A regression test that
goes red because an unrelated guard upstream rejects the input proves nothing about
the gap being closed.

The method is to **run the new or changed test against the code without the change
and watch it fail** on the target assertion — not to reason that it would fail, to
observe it. A test *believed* to depend on the change can be wrong about which
mechanism its assertion actually rests on; a test watched failing cannot. That gap
is the whole reason this rule exists, so the demonstration is an observation, never
an argument.

To put the code "without the change" back in front of the test:

- For a purely additive change — a new branch beside an untouched one — the
  pre-change path is often still reachable directly, and the test can be aimed at
  it with nothing to undo.
- Otherwise, back the change out of the working tree, run the test, confirm it
  fails on the target assertion, and restore the change.

What goes to review is the real output, not a description of it: the failing test's
name and its assertion diff exactly as pytest prints them, plus one line naming the
mechanism that failure corresponds to. The demonstrated red is produced by the same
pass that wrote the change, so the reviewer reading that raw output — not a summary
of it — is where the check actually becomes independent of the author. The review
is one judgement: does the red match the real defect.

### Arm B — genuinely new surface

A new route, a new module: the only "before" state is an import error or a 404.
That red is degenerate — it proves the code is new, nothing more.

Here the requirement is coverage of shape instead of a demonstrated failure:
enumerate every branch, guard, and validation the new code introduces, and cover
each one explicitly rather than only the happy path. The precedent for this style
of check is the storage layer's `@_synchronized` audit — walk the finite list of
places the property must hold and check each, rather than sampling.

Arm B is the rare case. Most changes are Arm A.

## Same-commit requirement

Unchanged: every change under `backend/src/analecta/**` carries its tests in the
same commit. A `check.sh` run reporting zero coverage on new backend code is a
blocker. TypeScript, Svelte, and Electron code are covered by manual QA only.

## The post-gate review pass

A green quality gate proves the new code does what it was told to do. It does not
prove that a test would notice if that code broke, nor that an existing invariant
survived. So after the gate passes, the demonstrated red is checked once more
against the change it belongs to: does that failure fall on the *specific* mechanism
the change closes, or on a nearby path that was never broken? For an Arm A change
this check is mandatory, not optional.

## What this policy deliberately does not add

- **No mutation-testing tool** — not in the gate, and not as a separate step scoped
  to changed lines. It is disproportionate for a project this size; the
  demonstrated-red rule and the post-gate pass cover the same ground for the changes
  where it matters. If this is ever revisited it stands on its own cost/benefit
  case, not on being bundled with this policy.
- **No failure output in the commit message**, and **no separate test-only commit
  before the fix.** The demonstrated failure is shown at review time, not recorded
  permanently.

## Scope

The same-commit test requirement is `backend/src/analecta/**`. The demonstrated-red
method and the Arm A / Arm B split apply to any regression or guard test in
`backend/tests/`, the Python helpers under `scripts/` included — they share the
`check.sh backend` gate, and the pattern this policy exists to catch first showed up
in `scripts/deps_update.py` tests.

Frontend and Electron testing is in `docs/quality-gate.md`.
