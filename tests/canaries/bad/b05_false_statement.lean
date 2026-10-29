-- canary: expect=failed theorem=clamp_strict
-- Off-by-one statement: clamp can return hi, so `< hi` is false.
import Apex.Prelude

def clamp (x lo hi : Int) : Int := if x < lo then lo else if x > hi then hi else x

theorem clamp_strict (x lo hi : Int) (h : lo ≤ hi) : clamp x lo hi < hi := by
  unfold clamp
  split <;> (try split) <;> omega
