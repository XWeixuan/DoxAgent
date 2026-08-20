Resolve each independent typed field task against only its supplied same-namespace candidates.

Judge canonical identity, not mere semantic relatedness. Use a contextual substitution test: the raw value and a candidate may LINK when replacing one with the other in the supplied context would still identify the same object. Allow different wording, abbreviations, normalized forms, and differences in non-discriminative detail.

Do not LINK when substitution changes the core object. Shared words, topic similarity, entailment, or a broader/narrower relationship alone do not establish identity.

Decide only one of:

* LINK: exactly one candidate represents the same canonical object; return its request-local short canonical_id (`k1`, `k2`, ...).
* NEW: the value is identifiable, but none of the candidates represents the same object.
* UNRESOLVED: the value itself cannot be identified from the supplied context.

Candidates are retrieval proposals, not presumed matches. Never link across namespaces.
Copy candidate IDs exactly. Never construct, expand, or transform an ID.
Return exactly one decision for every task ID. Tasks are independent; never omit, duplicate, or combine tasks.
Set `target_namespace` only for `participant.unknown` with decision `NEW`; otherwise return it as `null`.
