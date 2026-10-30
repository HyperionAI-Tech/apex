/-- P1: true exactly when every element is positive -/
theorem P1 (xs : List Int) : all_positive xs = true ↔ ∀ v ∈ xs, 0 < v := by
  sorry
