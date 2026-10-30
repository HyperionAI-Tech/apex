---- MODULE Spec ----
\* N workers each add K to a shared counter, reading and writing in separate steps.
EXTENDS Naturals, Sequences, TLC
CONSTANTS N, K
(* --algorithm workers
variables count = 0;
process worker \in 1..N
variables tmp = 0, i = 0;
begin
Loop:
  while i < K do
    Read:  tmp := count;
    Write: count := tmp + 1;
           i := i + 1;
  end while;
end process;
end algorithm; *)
\* BEGIN TRANSLATION
\* END TRANSLATION
AllDone == \A p \in 1..N : pc[p] = "Done"
NoLostUpdate == AllDone => count = N * K
NeverAhead == count <= N * K
====
