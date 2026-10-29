-- canary: expect=proved theorem=pyMax_comm
import Apex.Prelude

def pyMax (a b : Int) : Int := if a ≥ b then a else b

theorem pyMax_comm (a b : Int) : pyMax a b = pyMax b a := by
  unfold pyMax
  split <;> split <;> omega
