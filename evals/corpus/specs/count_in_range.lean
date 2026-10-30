/-- P1: the number of elements in [lo, hi] -/
theorem P1 (xs : List Int) (lo hi : Int) :
    count_in_range xs lo hi = ((xs.filter (fun v => lo ≤ v ∧ v ≤ hi)).length : Int) := by
  sorry
