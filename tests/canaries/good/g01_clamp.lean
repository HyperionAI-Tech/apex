-- canary: expect=proved theorem=clamp_bounds
import Apex.Prelude

def clamp (x lo hi : Int) : Int := if x < lo then lo else if x > hi then hi else x

theorem clamp_bounds (x lo hi : Int) (h : lo ≤ hi) :
    lo ≤ clamp x lo hi ∧ clamp x lo hi ≤ hi := by
  unfold clamp
  split <;> (try split) <;> omega
