
# Phase 5 Metrics

## Metrics Calculation Methods

### Success Rate
- **Definition**: Success rate represents the proportion of successful cases in each mode.
- **Formula**: Success Rate = (Number of successful cases) / (Total cases)
- **Source**: `report.json` -> success_rate field.

### Violation Rate
- **Definition**: The violation rate shows the frequency of hard violations in each mode.
- **Formula**: Violation Rate = (Number of violations) / (Total cases)
- **Source**: `report.json` -> violation_rate field.

### Average Fix Rounds
- **Definition**: The average number of fix rounds executed in each mode.
- **Formula**: Average Fix Rounds = (Total fix rounds) / (Total cases)
- **Source**: `report.json` -> avg_fix_rounds field.

### Latency
- **Definition**: The response time for each mode.
- **Formula**: Latency = (Total latency) / (Number of events)
- **Source**: `report.json` -> latency field.

### Closed Loop Rounds
- **Definition**: The number of rounds taken to complete a fix in the closed-loop mode.
- **Source**: `report.json` -> closed_loop_rounds field.
