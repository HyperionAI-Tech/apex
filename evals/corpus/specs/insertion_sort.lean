/-- P1: same length -/
theorem P1 (xs : List Int) : (insertion_sort xs).length = xs.length := by
  sorry

/-- P2: the result is sorted -/
theorem P2 (xs : List Int) : List.Pairwise (· ≤ ·) (insertion_sort xs) := by
  sorry

/-- P3: the result is a permutation of the input -/
theorem P3 (xs : List Int) : (insertion_sort xs).Perm xs := by
  sorry
