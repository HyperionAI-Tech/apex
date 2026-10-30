/-- P1: between zero and the number of pairs -/
theorem P1 (xs ys : List Int) :
    0 ≤ count_matches xs ys ∧ count_matches xs ys ≤ (xs.length : Int) * ys.length := by
  sorry
