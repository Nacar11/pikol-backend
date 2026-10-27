from typing import Annotated

from pydantic import StringConstraints

# For finding an account that already exists. EmailStr is for new addresses:
# it rejects reserved domains (.test, .local) that existing accounts may hold,
# and a lookup must not refuse an address the database already has. An
# unknown address still gets the endpoint's usual not-found answer.
LookupEmail = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=254)]
