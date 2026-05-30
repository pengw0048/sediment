# 001 Review Calibration Item

## Context

The schema section listed four review item types, while the review loop and
acceptance checklist require `calibration-only` as a fifth type.

## Decision

The implementation accepts `calibration_only` in `review_items.item_type` and
wires it through item generation.

## Consequences

This preserves the core review behavior and lets the verifier assert all five
item types are wired.
