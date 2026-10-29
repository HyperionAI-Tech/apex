-- canary: expect=failed theorem=uses_missing_lemma
import Apex.Prelude

theorem uses_missing_lemma (a b : Nat) : a + b = b + a := by
  exact Nat.add_comm_but_this_lemma_does_not_exist a b
