"""What a rule says of a sender or a domain. Here because Curation keeps the rules and the Assistant may suggest one, and neither
context imports the other. A request's or an answer's schema takes it as `Verdict`, so the page has it as a type and keeps only
its words for each."""
from typing import Literal

type Verdict = Literal["keep", "block"]
