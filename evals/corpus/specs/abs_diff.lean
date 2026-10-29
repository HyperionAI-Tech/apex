/-- P1: never negative -/
theorem P1 (a b : Int) : 0 ≤ abs_diff a b := by
  sorry

/-- P2: one of the two differences -/
theorem P2 (a b : Int) : abs_diff a b = a - b ∨ abs_diff a b = b - a := by
  sorry
