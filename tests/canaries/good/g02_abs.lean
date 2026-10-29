-- canary: expect=proved theorem=pyAbs_nonneg
import Apex.Prelude

def pyAbs (x : Int) : Int := if x < 0 then -x else x

theorem pyAbs_nonneg (x : Int) : 0 ≤ pyAbs x := by
  unfold pyAbs
  split <;> omega
