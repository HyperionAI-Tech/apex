-- canary: expect=proved theorem=em_or
-- Uses the three allowed axioms (propext, Classical.choice, Quot.sound); must still pass.
import Apex.Prelude

theorem em_or (p : Prop) : p ∨ ¬p := Classical.em p
