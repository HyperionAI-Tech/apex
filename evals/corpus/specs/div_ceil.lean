/-- P1: the result is the ceiling of a / b -/
theorem P1 (a b : Int) (h : 0 < b) :
    (div_ceil a b - 1) * b < a ∧ a ≤ div_ceil a b * b := by
  sorry
