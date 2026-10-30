/-- P1: the result is a multiple of m -/
theorem P1 (x m : Int) (h : 0 < m) : round_down x m % m = 0 := by
  sorry

/-- P2: the result is the largest multiple of m at most x -/
theorem P2 (x m : Int) (h : 0 < m) : round_down x m ≤ x ∧ x < round_down x m + m := by
  sorry
