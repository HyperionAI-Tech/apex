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

/-- Python `abs` on integers. -/
def pyAbs (x : Int) : Int := if x < 0 then -x else x

/-- Python `range(start, stop)` on integers (step 1). Empty when `stop ≤ start`. -/
def pyRange (start stop : Int) : List Int :=
  (List.range (stop - start).toNat).map (fun (i : Nat) => start + (i : Int))

/-- Python `sum` on a list of integers. -/
def pySum (xs : List Int) : Int := xs.foldl (· + ·) 0

@[simp] theorem pyAbs_def (x : Int) : pyAbs x = if x < 0 then -x else x := rfl
@[simp] theorem pySum_nil : pySum [] = 0 := rfl

/-- Renders a value as a Python literal, so the fidelity bridge can compare Lean model outputs
with the real Python using `ast.literal_eval`. Tuples print right-nested, as Lean stores them. -/
class PyRepr (α : Type) where
  pyRepr : α → String

export PyRepr (pyRepr)

instance : PyRepr Int := ⟨fun n => toString n⟩
instance : PyRepr Nat := ⟨fun n => toString n⟩
instance : PyRepr Bool := ⟨fun b => if b then "True" else "False"⟩
instance {α : Type} [PyRepr α] : PyRepr (List α) :=
  ⟨fun xs => "[" ++ ", ".intercalate (xs.map pyRepr) ++ "]"⟩
instance {α β : Type} [PyRepr α] [PyRepr β] : PyRepr (α × β) :=
  ⟨fun p => "(" ++ pyRepr p.1 ++ ", " ++ pyRepr p.2 ++ ")"⟩
instance {α : Type} [PyRepr α] : PyRepr (Option α) :=
  ⟨fun o => match o with
    | none => "None"
    | some a => pyRepr a⟩

end Apex
