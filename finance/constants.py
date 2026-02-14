from decimal import Decimal

# Flat monthly penalty (default mode)
FIXED_MONTHLY_PENALTY = Decimal("100.00")

# Percentage model (for future)
PENALTY_RATE_PERCENT = Decimal("5.0")

# Toggle (easy future switch)
PENALTY_MODE = "FIXED"  # FIXED | RATE
from decimal import Decimal

MIN_MONTHLY_SAVING = Decimal("500.00")
