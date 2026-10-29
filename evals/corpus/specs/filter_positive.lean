/-- P1: every kept element is positive -/
theorem P1 (xs : List Int) : ∀ v ∈ filter_positive xs, 0 < v := by
  sorry

/-- P2: never longer than the input -/
theorem P2 (xs : List Int) : (filter_positive xs).length ≤ xs.length := by
  sorry
