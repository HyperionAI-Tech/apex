/-- P1: never above the cap -/
theorem P1 (a b cap : Int) : bounded_add a b cap ≤ cap := by
  sorry

/-- P2: the plain sum when it fits -/
theorem P2 (a b cap : Int) (h : a + b ≤ cap) : bounded_add a b cap = a + b := by
  sorry
