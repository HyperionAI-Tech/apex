/-- The loop counter never decreases and grows by at most one per element. -/
theorem count_fold_bounds (xs : List Int) (n : Int) :
    n ≤ xs.foldl (fun n x => if x > 0 then n + 1 else n) n ∧
      xs.foldl (fun n x => if x > 0 then n + 1 else n) n ≤ n + xs.length := by
  induction xs generalizing n with
  | nil => simp
  | cons y ys ih =>
    simp only [List.foldl_cons, List.length_cons]
    split
    · have := ih (n + 1); omega
    · have := ih n; omega

/-- P1: the count is never negative -/
theorem P1 (xs : List Int) : 0 ≤ count_positive xs := by
  simp only [count_positive]
  exact (count_fold_bounds xs 0).1

/-- P2: the count never exceeds the length -/
theorem P2 (xs : List Int) : count_positive xs ≤ xs.length := by
  simp only [count_positive]
  have := (count_fold_bounds xs 0).2
  omega
