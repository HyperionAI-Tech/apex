/-- P1: the empty sequence is balanced -/
theorem P1 : balanced [] = true := by
  sorry

/-- P2: a balanced sequence opens as often as it closes -/
theorem P2 (xs : List Int) (h : balanced xs = true) :
    (xs.filter (fun s => 0 < s)).length = (xs.filter (fun s => ¬ 0 < s)).length := by
  sorry
