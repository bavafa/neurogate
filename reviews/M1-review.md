## Summary

**M1 needs fixes before approval.** SPEC has no `[PROPOSED]` markers, all original questions have recorded answers, and register fields match SPEC. No files edited.

After `. scripts/env.sh`, both checks exited 0:

```text
lint: no RTL files yet (nothing to check)
test: no tests yet (nothing to run)
```

These are empty-tree checks, not functional verification.

## Must fix

1. **docs/regmap.md:13–15; SPEC.md:170–172 — counter guarantee is incorrect.** Counters update after queued entries finish, so their sum cannot always equal frames received. Soft reset clears counters without flushing queued entries or pending overflow counts; pre-reset frames can therefore appear in post-reset counts. Define reset precedence and accounting boundaries, and qualify the equality for pipeline drain and saturation.

2. **SPEC.md:147,178; DECISIONS.md D-002 — test-set policy contradicts threshold selection.** The test set is “never used for any tuning,” but the default threshold is selected from its margin/accuracy table. Decide on validation data for threshold selection or explicitly revise the evaluation policy.

3. **scripts/toolchain_check.sh:17–24 — failed version commands report success.** The script checks executable presence but ignores command failure. During review it printed `Icarus: … Error opening temporary file <temporary directory>/…` and still exited 0. Propagate failures and identify unusable version output.

## Should fix

- **docs/stories.md:58:** “two sources sharing one history slot” changes the required test. SPEC requires two packets sharing a destination-port bucket, with one leaving the window while another remains.
- **docs/PRD.md:5,14; docs/stories.md:32,85:** AI and IP remain unexplained; “IPv4 TCP or UDP … packet types” does not explain the individual terms. No decision IDs or task tags found; README is explained where named.
- **STATUS.md:25,45:** OpenSTA loading “135 cells” and the deleted-module compile check have **no evidence** beyond narrative assertions. Preserve command output or qualify them as unverified historical reports.
- **SPEC.md:130,137; README.md:5–6:** The golden model and verification are described as existing, although neither exists yet. Use planned wording; label 176 cycles as an architectural estimate rather than measured latency.

## Nits

- **STATUS.md:82:** Resume instructions select stale branch `m1-scaffold`.
- **README.md:8,33:** “in progress” conflicts with “not started.” The table follows the literal M1 task, but is stale.
- No unauthorized scope creep found: the extra documentation work is authorized by D-007; scaffolding additions are recorded in STATUS.

## Gate checklist

| Item | Status |
|---|---|
| SPEC frozen; no `[PROPOSED]` remains | **met** |
| Register map final and consistent | **not met** — accounting/reset semantics need resolution |
| `make lint` run | **met** — empty tree, exit 0 |
| `make test` run | **met** — empty tree, exit 0 |
| Toolchain versions recorded with real output | **met** — STATUS contains output; current checker has the failure above |
| Every original open question answered in DECISIONS | **met** — D-001a–e and D-006 |
| Liberty file decided | **met** — Nangate45 typical; path and checksum recorded |
| Functional RTL, synthesis or timing results | **no evidence** — not required at M1 |