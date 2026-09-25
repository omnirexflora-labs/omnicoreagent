"""What the runtime asks when a model says it is done and review is on.

A model's "done" is a claim. In one real trial the model reported that invalid
input was handled after trying a single invalid input; eight others failed. The review asks for evidence, not reassurance: each
requirement, and the check this run made that shows it holds.
"""

COMPLETION_REVIEW_PROMPT = (
    "Before this is final, review it against the task. List each requirement the "
    "task sets, including those in any specification or document it points to, and "
    "beside each one the command or check you ran in this run that shows it holds. "
    "For any requirement with no such check, run one now and fix what fails. Do not "
    "report a check you did not run. When every requirement is shown to hold, or you "
    "have said plainly which ones do not and why, give your final answer again."
)
