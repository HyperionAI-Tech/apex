/-- P1: each run expands to count copies of its value -/
theorem P1 (runs : List (Int × Int)) :
    rle_decode runs = runs.flatMap (fun p => List.replicate p.2.toNat p.1) := by
  sorry
