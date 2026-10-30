/-- P1: every element of the result is even -/
theorem P1 (xs : List Int) : ∀ v ∈ evens xs, v % 2 = 0 := by
  sorry

/-- P2: it is the filter of the even elements -/
theorem P2 (xs : List Int) : evens xs = xs.filter (fun v => v % 2 = 0) := by
  sorry
