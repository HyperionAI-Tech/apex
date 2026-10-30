/-- P1: never longer -/
theorem P1 (xs : List Int) : (dedup_adjacent xs).length ≤ xs.length := by
  sorry

/-- P2: empty exactly for the empty list -/
theorem P2 (xs : List Int) : dedup_adjacent xs = [] ↔ xs = [] := by
  sorry
