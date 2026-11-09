# Backend cleanup: batch 1

This batch records the starting point before application code is reorganized.
It completes the first three agreed tasks:

1. [Starting-version record](01-baseline.md).
2. [Current backend map](02-backend-map.md).
3. [Test assessment and known gaps](03-test-assessment.md).

The source branch is `standalone-admin`; the cleanup branch is
`backend_code_cleaning`. These are the repository's actual branch names.

This batch changes documentation only. It does not change application behavior,
permissions, database structure, stored records, or production configuration.
The assessment separates existing test failures from cleanup regressions.

Further cleanup should be delivered in batches of three tasks, with focused
validation and independently reviewable commits. This record is a baseline,
not evidence that all existing workflows are covered or that the full suite
passes. Read the assessment before using a failing test as a regression signal.
