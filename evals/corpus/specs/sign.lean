/-- P1: the result is -1, 0 or 1 -/
theorem P1 (x : Int) : sign x = -1 ∨ sign x = 0 ∨ sign x = 1 := by
  sorry

/-- P2: positive inputs give 1 -/
theorem P2 (x : Int) (h : 0 < x) : sign x = 1 := by
  sorry

/-- P3: sign x * x is never negative -/
theorem P3 (x : Int) : 0 ≤ sign x * x := by
  sorry

/-- P4: zero gives 0 -/
theorem P4 : sign 0 = 0 := by
  sorry

/-- P5: negative inputs give -1 -/
theorem P5 (x : Int) (h : x < 0) : sign x = -1 := by
  sorry
