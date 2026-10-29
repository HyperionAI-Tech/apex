/-- P1: the result is never negative -/
theorem P1 (x : Int) : 0 ≤ my_abs x := by
  sorry

/-- P2: the result is x or -x -/
theorem P2 (x : Int) : my_abs x = x ∨ my_abs x = -x := by
  sorry

/-- P3: symmetric under negation -/
theorem P3 (x : Int) : my_abs (-x) = my_abs x := by
  sorry
