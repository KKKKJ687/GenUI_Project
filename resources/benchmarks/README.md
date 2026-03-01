
# Benchmark Suite

This benchmark suite contains test cases for validating the HMI system's behavior in different modes and risk scenarios.

## Structure:
- **suite_v1.jsonl**: The benchmark suite, with 30 samples categorized by difficulty and risk type.
- **checks_spec.md**: Defines the check types and their structure.
- **README.md**: This file, explaining the suite and its usage.

## Running the Suite:
To execute the suite, ensure that the system is running with the correct environment and dependencies.

1. Load the benchmark suite from `suite_v1.jsonl`.
2. Execute each test sample based on its specified mode targets (e.g., baseline, verifier).
3. Check the results based on the expected checks.

## Extending the Suite:
You can add new samples by following the format in `suite_v1.jsonl`. Ensure each sample has an `expected_checks` section and correct tags for difficulty and risk type.
