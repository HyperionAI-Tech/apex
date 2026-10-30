/-- P1: a non-empty list's maximum is an element -/
theorem P1 (xs : List Int) (d : Int) (h : xs ≠ []) : max_or xs d ∈ xs := by
  sorry

/-- P2: a non-empty list's maximum bounds every element -/
theorem P2 (xs : List Int) (d : Int) (h : xs ≠ []) : ∀ v ∈ xs, v ≤ max_or xs d := by
  sorry

/-- P3: the default for the empty list -/
theorem P3 (d : Int) : max_or [] d = d := by
  sorry
