/-- P1: never negative -/
theorem P1 (a b : Int) (ha : 0 ≤ a) (hb : 0 ≤ b) : 0 ≤ saturating_sub a b := by
  sorry

/-- P2: ordinary subtraction when it does not go below zero -/
theorem P2 (a b : Int) (h : b ≤ a) : saturating_sub a b = a - b := by
  sorry

/-- P3: zero when b exceeds a -/
theorem P3 (a b : Int) (h : a < b) : saturating_sub a b = 0 := by
  sorry
