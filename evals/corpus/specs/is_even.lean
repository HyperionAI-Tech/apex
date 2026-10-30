/-- P1: even exactly when 2 divides n -/
theorem P1 (n : Int) : is_even n = true ↔ 2 ∣ n := by
  sorry

/-- P2: adding 2 keeps the parity -/
theorem P2 (n : Int) : is_even (n + 2) = is_even n := by
  sorry
