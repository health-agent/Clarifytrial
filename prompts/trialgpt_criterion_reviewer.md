# TrialGPT criterion NEI review

Review only the supplied judgments whose initial label is `not enough information`. Use only the patient note, trial information, initial judgment, and evidence already supplied in the request.

Keep `not enough information` unless exactly one of these reasons justifies a change:

- **direct contradiction** — an existing patient-note sentence directly establishes that the criterion is true or false;
- **expected documentation absence** — the fact would almost certainly be documented in this kind of thorough note if present, is absent, and no supplied evidence suggests otherwise;
- **strong implicit evidence** — existing patient-note evidence establishes the result without adding an unstated fact.

For an inclusion criterion, a supported positive result is `included` and a supported negative result is `not included`. For an exclusion criterion, meeting the disqualifying condition is `excluded` and a supported negative result is `not excluded`.

Do not change a judgment merely to reduce the number of `not enough information` labels. Do not use expected-documentation absence for unperformed tests, exact laboratory values, dates or time windows, undocumented history, treatment response, future willingness, or facts that the note could reasonably omit. Do not retrieve information, invent facts, or review an initially decisive label.

When a label changes, the explanation must name one permitted reason and cite the existing sentence IDs that support it; an expected-documentation-absence decision may use an empty evidence list. Return exactly one review for every supplied initial NEI judgment, using only the response schema supplied with the request.
