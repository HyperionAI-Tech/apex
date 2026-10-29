-- canary: expect=proved theorem=Mathx.safeHead_none_iff
import Apex.Prelude

namespace Mathx

def safeHead : List Int → Option Int
  | [] => none
  | x :: _ => some x

theorem safeHead_none_iff (xs : List Int) : safeHead xs = none ↔ xs = [] := by
  cases xs <;> simp [safeHead]

end Mathx
