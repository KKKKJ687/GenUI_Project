
# Checks Specification

## Supported Check Types:
- **widget_type**: Checks the type of widget (e.g., slider, switch).
- **param_compare**: Compares a parameter against a threshold or range (e.g., <=, >=).
- **metrics**: Verifies whether specific metrics (e.g., violations count, hard violations) are within expected limits.
- **binding_contains**: Checks that required protocol fields are present and correctly formatted.

Each check must be represented as a JSON object, which will be parsed and executed by the test scripts.
