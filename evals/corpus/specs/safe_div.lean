/-- P1: dividing by zero returns None -/
theorem P1 (a : Int) : safe_div a 0 = none := by
  sorry

/-- P2: otherwise it is Python floor division -/
theorem P2 (a b : Int) (h : b ≠ 0) : safe_div a b = some (Apex.pyFloorDiv a b) := by
  sorry
