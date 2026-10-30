/-- P1: the factorial of a natural number is at least 1 -/
theorem P1 (n : Int) (h : 0 ≤ n) : 1 ≤ fact_loop n := by
  sorry

/-- P2: 0! = 1 -/
theorem P2 : fact_loop 0 = 1 := by
  sorry

/-- P3: (n + 1)! = (n + 1) * n! -/
theorem P3 (n : Int) (h : 0 ≤ n) : fact_loop (n + 1) = (n + 1) * fact_loop n := by
  sorry
