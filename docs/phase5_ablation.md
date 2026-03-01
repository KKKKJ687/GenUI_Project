
# Phase 5 Ablation Study

## How to Perform an Ablation Study

An ablation study allows us to understand the contribution of each system component to the overall performance. This is done by disabling or altering certain components and measuring their impact on key metrics such as success rate, violation rate, fix rate, latency, and closed loop rounds.

## Report Structure

The ablation study generates the following outputs:

- **`ablation_report.json`**: Contains the summarized results for each experiment group.
- **`ablation_summary.png`**: A bar chart comparing success rates across different experimental configurations.
- **`ablation_details.csv`**: Detailed per-case data for each experiment configuration.

### Ablation Metrics

The following metrics are analyzed in the study:

1. **Success Rate**: Proportion of successful cases in each experimental setup.
2. **Violation Rate**: Rate of hard violations in each experiment.
3. **Fix Rate**: Rate of successful fixes in each setup.
4. **Closed Loop Rounds**: Number of iterations required for closed-loop fixes.
5. **Latency**: The average response time for each mode.

## Example Ablation Results

- **Success Rate**: A comparison of success rates across different experimental configurations.
- **Violations**: Evaluation of how each configuration impacts violations.
- **Closed Loop Performance**: Tracking closed-loop repair rounds and their efficiency.
