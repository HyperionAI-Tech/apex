/-- P1: counts the elements equal to v -/
theorem P1 (xs : List Int) (v : Int) : count_eq xs v = (xs.filter (fun x => decide (x = v))).length := by
  sorry
