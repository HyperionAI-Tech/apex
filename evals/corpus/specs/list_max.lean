/-- P1: the maximum bounds every element -/
theorem P1 (xs : List Int) (m : Int) (h : list_max xs = some m) : ∀ v ∈ xs, v ≤ m := by
  sorry

/-- P2: the maximum is an element -/
theorem P2 (xs : List Int) (m : Int) (h : list_max xs = some m) : m ∈ xs := by
  sorry

/-- P3: None exactly for the empty list -/
theorem P3 (xs : List Int) : list_max xs = none ↔ xs = [] := by
  sorry
