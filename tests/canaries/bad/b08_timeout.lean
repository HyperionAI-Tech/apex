-- canary: expect=timeout theorem=slow timeout=20
-- A bounded but very expensive kernel decision: ~2.25M cases by `decide`.
import Apex.Prelude

set_option maxHeartbeats 0 in
set_option maxRecDepth 100000 in
theorem slow : ∀ a < 1500, ∀ b < 1500, a * b ≠ 2250001 := by
  decide
