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

## Why ordering alone isn't the safeguard

Test-first ordering (write the failing test, then the code) is not, by itself,
what makes a demonstrated failure trustworthy. Its value was never the ordering —
it's the moment in the middle: a failing test, observed and understood, before the
fix exists. Order without independence doesn't secure that moment: when the same
authorship produces both the test and the code it checks in one continuous pass,
the ordering is trivial to satisfy while skipping the moment entirely — a test
written against already-working code can pass on the first run without ever
touching the behaviour it was meant to protect.

What secures the moment is whether the test's design was produced independently of
the code it checks — before that code exists, by an author who never sees or writes
it. That independence is the default this policy requires for Arm A:

- **Independent authorship, test-first (default).** The test is designed and
  approved before the implementation exists, by an author with no sight of the fix.
  Here HEAD already is the pre-change state — nothing to reconstruct — and the
  ordering carries real information, because it's backed by that independence, not
  by ceremony. This is the assumed method for every Arm A change unless waived.
- **Single pass (exception, decided per change).** The same authorship produces
  test and code together. This does not reach independence — it's a different,
  weaker demonstration: the test's content was already shaped by an author who knew
  the fix, and only the mechanical act of observing the failure is left to carry
  information. It applies only where a specific change has been individually
  triaged as not warranting the default — never as a silent fallback, and never
  because a change merely looks small. Where it applies, ordering is irrelevant and
  dropped; the alternative demonstration below is required instead.

Which changes get the default versus the exception, and how independent authorship
is arranged and verified, is operational detail outside this document's scope. What
this policy fixes is the requirement itself: whichever method produced the test, it
must be shown to fail for the right reason, whenever a state exists in which it
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

To put the code "without the change" in front of the test:

- **Default — independent authorship.** The test was designed and approved before
  the implementation existed. HEAD already is the pre-change state; there is
  nothing to reconstruct. The red observed at approval time is the demonstration.
- **Exception — single pass, purely additive change.** A new branch beside an
  untouched one: the pre-change path is often still reachable directly, and the
  test can be aimed at it with nothing to undo.
- **Exception — single pass, otherwise.** Back the change out of the working tree,
  run the test, confirm it fails on the target assertion, and restore the change.

What goes to review is the real output, not a description of it: the failing test's
name and its assertion diff exactly as pytest prints them, plus one line naming the
mechanism that failure corresponds to. Under the default method, independence is
already structural — the test's author never saw the fix — but the raw output still
has to be read verbatim rather than taken on the author's word: a claim that a test
failed for the right reason is not itself evidence. Under the exception, the
demonstrated red is produced by the same pass that wrote the change, so the reviewer
reading that raw output is where the check becomes independent of the author at all.
Either way, the review is one judgement: does the red match the real defect.

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
prove that the solution is complete, or that nothing relevant was missed — by
whoever designed the test, whoever approved it, or whoever implemented the fix.
Designing tests and an implementation that pass is not the same claim as the
implementation being sufficient and satisfactory: the same blind spot can sit in
every role at once, because a green run only confirms the code does what the tests
check, not that the tests checked what mattered.

So once the gate is green, a review independent of that green run is mandatory for
every Arm A change: does the implementation actually address the scenario it was
meant to, and is there an edge case, an interaction, or a consequence that nobody
involved contemplated. This is not a re-check of whether the red matched the
defect — that was already settled when the test was approved (default method) or
observed (exception). It is a check for gaps a passing suite cannot surface on its
own.

## What this policy deliberately does not add

- **No mutation-testing tool** — not in the gate, and not as a separate step scoped
  to changed lines. It is disproportionate for a project this size: the
  demonstrated-red rule covers whether a test targets the right mechanism, and the
  post-gate review covers whether the implementation is complete — between the two,
  the ground mutation testing would cover is already claimed for the changes where
  it matters. If this is ever revisited it stands on its own cost/benefit case, not
  on being bundled with this policy.
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
