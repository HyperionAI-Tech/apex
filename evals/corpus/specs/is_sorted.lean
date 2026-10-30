/-- P1: true exactly for lists in ascending order -/
theorem P1 (xs : List Int) : is_sorted xs = true ↔ List.Pairwise (· ≤ ·) xs := by
  sorry
