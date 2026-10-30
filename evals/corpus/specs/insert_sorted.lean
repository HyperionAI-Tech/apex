/-- P1: one element longer -/
theorem P1 (x : Int) (xs : List Int) : (insert_sorted x xs).length = xs.length + 1 := by
  sorry

/-- P2: inserting into a sorted list keeps it sorted -/
theorem P2 (x : Int) (xs : List Int) (h : List.Pairwise (· ≤ ·) xs) :
    List.Pairwise (· ≤ ·) (insert_sorted x xs) := by
  sorry

/-- P3: the result is a permutation of x :: xs -/
theorem P3 (x : Int) (xs : List Int) : (insert_sorted x xs).Perm (x :: xs) := by
  sorry
