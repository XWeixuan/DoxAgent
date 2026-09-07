# Closed-cycle final trade selection

Compare the frozen trade candidates for one ticker against the latest supplied
Reality/Reference View and full PolicySet. Candidate evidence records why a trade
was justified at the time; it is not proof that the premise remains valid now.

Select at most one existing candidate that still merits execution, or select none.
Do not create a trade, invent a Policy, change the candidate's direction, or infer
that a candidate was executed. Earlier realtime trade outputs are context only:
exclude duplicate or conflicting candidates; they do not join the competition.

For Policy candidates, verify that the relevant current Policy and its economic
premise still support the original action. For expert-origin candidates, compare
their actual evidence and current relevance without inventing a policy_id.
Missing or contradictory evidence favors no selection. Return only the requested
JSON with selection_id, candidate_id (null for no trade), and a concise reason.
