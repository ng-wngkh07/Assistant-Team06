## What
<one or two sentences>

## Why
Closes #<issue>. <use case / acceptance criteria>

## How tested
- [ ] Portable suite: `python -m pytest -c pytest.windows.ini` (or N/A with reason)
- [ ] Relevant UI/feature checks; record OS, Python, profile, results and NOT_RUN
- [ ] manual check: <steps>

## AI use
<tool, what it produced, what you changed, how you verified — or "None">

## Definition of Done
- [ ] acceptance criteria met
- [ ] tests added or updated
- [ ] no secrets or personal data
- [ ] README / docs updated if behaviour changed
- [ ] reviewed and approved by the assigned reviewer
- [ ] new entry in `LICH_SU_DU_AN.md`; previous entries preserved
- [ ] all deliverables appear in the diff; check ignored files as well
- [ ] no private source, training information, database, weights or local settings
- [ ] shared changes: affected owners coordinated; lookup / QA / timetable checked

See [Definition of Done](../DEFINITION_OF_DONE.md). Tests for documentation-only
changes may be N/A with an explanation. Owner merges after current CI and review.
