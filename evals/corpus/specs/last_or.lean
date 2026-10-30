/-- P1: the default for the empty list -/
theorem P1 (d : Int) : last_or [] d = d := by
  sorry

/-- P2: the last element otherwise -/
theorem P2 (xs : List Int) (x d : Int) : last_or (xs ++ [x]) d = x := by
  sorry
