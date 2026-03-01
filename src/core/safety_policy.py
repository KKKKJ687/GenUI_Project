class SafetyPolicy:
    def __init__(self, max_value, min_value, acceptable_range, failure_mode):
        self.max_value = max_value
        self.min_value = min_value
        self.acceptable_range = acceptable_range
        self.failure_mode = failure_mode

    def check_value(self, value):
        if value < self.min_value or value > self.max_value:
            return False
        return value in self.acceptable_range

    def handle_failure(self, value):
        if self.failure_mode == 'reject':
            raise ValueError(f"Value {value} is out of acceptable range.")
        elif self.failure_mode == 'warning':
            print(f"Warning: Value {value} is out of acceptable range.")
        # Add other failure modes as needed
