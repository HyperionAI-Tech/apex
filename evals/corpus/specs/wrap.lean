/-- P1: the result is in [0, n) -/
theorem P1 (x n : Int) (h : 0 < n) : 0 ≤ wrap x n ∧ wrap x n < n := by
  sorry

/-- P2: the result is congruent to x modulo n -/
theorem P2 (x n : Int) (h : 0 < n) : Apex.pyMod (x - wrap x n) n = 0 := by
  sorry
