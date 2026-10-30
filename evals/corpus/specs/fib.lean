/-- P1: fib 0 = 0 -/
theorem P1 : fib 0 = 0 := by
  sorry

/-- P2: fib 1 = 1 -/
theorem P2 : fib 1 = 1 := by
  sorry

/-- P3: the Fibonacci recurrence -/
theorem P3 (n : Int) (h : 0 ≤ n) : fib (n + 2) = fib (n + 1) + fib n := by
  sorry
