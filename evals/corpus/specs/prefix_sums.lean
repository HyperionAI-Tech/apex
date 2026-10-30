/-- P1: same length -/
theorem P1 (xs : List Int) : (prefix_sums xs).length = xs.length := by
  sorry

/-- P2: appending an element appends the new total -/
theorem P2 (xs : List Int) (x : Int) : prefix_sums (xs ++ [x]) = prefix_sums xs ++ [xs.sum + x] := by
  sorry
