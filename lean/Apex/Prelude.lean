/-
Apex prelude: shared definitions imported by every generated model file.

Deliberately core-only. Every verdict is a cold compile, so whatever this file imports is paid on
every check; `omega`, `simp` and `decide` are in core. Generated files import Mathlib modules
themselves, and only the ones a given proof needs.
-/

namespace Apex

/-- Python floor division on integers (`//`), which rounds toward negative infinity. -/
def pyFloorDiv (a b : Int) : Int := Int.fdiv a b

/-- Python modulo on integers (`%`), whose sign follows the divisor. -/
def pyMod (a b : Int) : Int := Int.fmod a b

end Apex
