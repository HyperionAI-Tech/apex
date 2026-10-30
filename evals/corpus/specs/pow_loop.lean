/-- P1: b ^ 0 = 1 -/
theorem P1 (b : Int) : pow_loop b 0 = 1 := by
  sorry

/-- P2: one more factor of b per step -/
theorem P2 (b e : Int) (h : 0 ≤ e) : pow_loop b (e + 1) = pow_loop b e * b := by
  sorry

/-- P3: it agrees with Lean's power -/
theorem P3 (b e : Int) (h : 0 ≤ e) : pow_loop b e = b ^ e.toNat := by
  sorry
