# Presenting statements for human approval

A wrong proof fails loudly; a wrong statement passes silently. That is why a person approves
each theorem statement before Apex calls it `proven`.

How to present them:

1. Run `apex review <target>` (or `apex_review_status`) to list statements with their status.
2. For each pending statement show, side by side: the property in plain English (the doc comment,
   which names where the intent came from) and the Lean statement exactly as Apex printed it.
3. Point out anything the user should check: preconditions the statement assumes (`lo ≤ hi →`),
   how `None` results are handled (`r = some v`), integer vs natural number casts (`(xs.length : Int)`).
4. Ask the user to run, in their own terminal:

       apex review <target> --approve P1,P2

   The command asks for typed confirmation and refuses a non-interactive shell, so you cannot run
   it for them. If no person is available, continue: results stay `proven_unreviewed`.

Never edit `.apex/reviews.json`. Never reword a statement to make it easier to prove; a reworded
statement needs new approval, and Apex lapses the old one automatically.
