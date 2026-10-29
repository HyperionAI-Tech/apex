/-- P1: the empty list sums to 0 -/
theorem P1 : sum_list [] = 0 := by
  sorry

/-- P2: appending an element adds it to the sum -/
theorem P2 (xs : List Int) (x : Int) : sum_list (xs ++ [x]) = sum_list xs + x := by
  sorry

/-- P3: agrees with Python's built-in sum -/
theorem P3 (xs : List Int) : sum_list xs = Apex.pySum xs := by
  sorry
